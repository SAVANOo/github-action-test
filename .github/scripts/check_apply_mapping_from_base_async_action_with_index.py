import os
import re
import sys
import requests
from github import Github

BASE_ASYNC_ACTION_EXTENSION_REGEX = re.compile(r"class\s+([\w\d_]+)\s+extends\s+BaseAsyncAction")
APPLY_BASE_MAPPING_REGEX = re.compile(r"\bapplyBaseMapping\b")
APPLY_BASE_MAPPING_WITH_INDEX_REGEX = re.compile(r"\bapplyBaseMappingWithIndex\b")

COMMENT_MESSAGE_TEMPLATE = """
Troca de `applyBaseMapping` para `applyBaseMappingWithIndex`:

A classe `{class_name}` no arquivo `{file_path}` foi modificada para usar `applyBaseMappingWithIndex` em vez de `applyBaseMapping`.

**Antes de realizar o deploy, por favor, garanta que realizou:**

- [ ] Script pré deploy adicionando via DBA os índices necessários para a `{class_name}`.

[!IMPORTANT]
Ao migrar de `applyBaseMapping` para `applyBaseMappingWithIndex`, é necessário garantir que os índices necessários estejam criados no banco de dados para evitar problemas de locks durante o deploy.
"""

def get_github_pr_details():
    token = os.getenv('GITHUB_TOKEN')
    repository_name = os.getenv('GITHUB_REPOSITORY')
    github_ref = os.getenv('GITHUB_REF')

    if not all([token, repository_name, github_ref]):
        print("Erro: Variáveis de ambiente GITHUB_TOKEN, GITHUB_REPOSITORY ou GITHUB_REF não definidas.")
        sys.exit(1)

    try:
        pr_number_str = github_ref.split('/')[-2]
        pr_number = int(pr_number_str)
    except (IndexError, ValueError):
        print(f"Erro: Não foi possível extrair o número do PR de GITHUB_REF: {github_ref}")
        print("Este script foi projetado para rodar em eventos de pull_request.")
        sys.exit(0)

    g = Github(token)
    repo_obj_pygithub = g.get_repo(repository_name)
    pr_obj = repo_obj_pygithub.get_pull(pr_number)

    return pr_obj, repo_obj_pygithub, token

def get_pr_files_via_api(repository_name, pr_number, token):
    url = f"https://api.github.com/repos/{repository_name}/pulls/{pr_number}/files"
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()

def get_file_content_from_repo(repo_obj_pygithub, file_path, sha):
    try:
        content_file = repo_obj_pygithub.get_contents(file_path, ref=sha)
        return content_file.decoded_content.decode('utf-8')
    except Exception as e:
        print(f"Aviso: Não foi possível obter conteúdo do arquivo {file_path} no SHA {sha}. Erro: {e}")
        return None

def get_class_name_if_extends_base_async(file_content):
    match = BASE_ASYNC_ACTION_EXTENSION_REGEX.search(file_content)
    return match.group(1) if match else None

def check_base_async_action_migration(pr_obj, repo_obj_pygithub, files_from_api, existing_comments_bodies):
    files_requiring_comment = []

    for file_data_from_api in files_from_api:
        filename = file_data_from_api['filename']
        status = file_data_from_api['status']
        patch_content = file_data_from_api.get('patch')

        if not filename.endswith('.groovy') or status != 'modified':
            continue

        print(f"Verificando arquivo modificado: {filename}")

        current_content = get_file_content_from_repo(repo_obj_pygithub, filename, pr_obj.head.sha)
        previous_content = get_file_content_from_repo(repo_obj_pygithub, filename, pr_obj.base.sha)

        if not current_content or not previous_content:
            print(f"  Conteúdo atual ou anterior de {filename} não encontrado. Pulando.")
            continue

        class_name = get_class_name_if_extends_base_async(current_content)
        if not class_name:
            print(f"  Arquivo {filename} não estende BaseAsyncAction ou nome da classe não encontrado. Pulando.") # Adicionado print
            continue
        print(f"  Arquivo {filename} estende BaseAsyncAction (Classe: {class_name}).")

        uses_with_index_now = bool(APPLY_BASE_MAPPING_WITH_INDEX_REGEX.search(current_content))
        used_simple_before = bool(APPLY_BASE_MAPPING_REGEX.search(previous_content))

        # DEBUG PRINTS:
        print(f"    DEBUG: current_content contém 'applyBaseMappingWithIndex'? {uses_with_index_now}")
        # print(f"    DEBUG: current_content (primeiros 200 chars): {current_content[:200]}") # Descomente se necessário
        print(f"    DEBUG: previous_content contém 'applyBaseMapping'? {used_simple_before}")
        # print(f"    DEBUG: previous_content (primeiros 200 chars): {previous_content[:200]}") # Descomente se necessário


        if not (uses_with_index_now and used_simple_before):
            print(f"    DEBUG: Condição (uses_with_index_now AND used_simple_before) NÃO atendida. Pulando para o próximo arquivo.")
            continue

        print(f"  Arquivo {filename}: USA 'WithIndex' agora E USAVA 'Simple' antes.")

        migrated_in_patch = False
        if patch_content:
            removed_simple_in_patch = any(
                line.startswith('-') and APPLY_BASE_MAPPING_REGEX.search(line)
                for line in patch_content.split('\n')
            )
            added_with_index_in_patch = any(
                line.startswith('+') and APPLY_BASE_MAPPING_WITH_INDEX_REGEX.search(line)
                for line in patch_content.split('\n')
            )

            # DEBUG PRINTS PATCH:
            print(f"      DEBUG: Analisando patch para {filename}:")
            # print(f"      DEBUG: Patch content (primeiras 5 linhas):\n{patch_content.splitlines()[:5]}") # Descomente se necessário
            print(f"      DEBUG: Linha removida com 'applyBaseMapping' encontrada no patch? {removed_simple_in_patch}")
            print(f"      DEBUG: Linha adicionada com 'applyBaseMappingWithIndex' encontrada no patch? {added_with_index_in_patch}")

            if removed_simple_in_patch and added_with_index_in_patch:
                migrated_in_patch = True
                print(f"  Detectada migração explícita no patch de {filename}.")
            else: # Adicionado para clareza
                print(f"  Migração NÃO detectada explicitamente no patch (removed_simple={removed_simple_in_patch}, added_with_index={added_with_index_in_patch}).")


        if not migrated_in_patch:
            # Esta lógica de fallback é um pouco mais arriscada, vamos mantê-la simples por enquanto
            # Se o patch existia mas não confirmou, provavelmente não deveríamos usar o fallback.
            # O fallback é mais para quando o patch não está disponível.
            if not patch_content and not APPLY_BASE_MAPPING_REGEX.search(current_content):
                 print(f"  Detectada migração (baseada na ausência do método antigo, SEM patch) em {filename}.")
                 migrated_in_patch = True # Reutilizando a flag
            else:
                # Se o patch existia e não confirmou, ou se o patch não existia E o método antigo ainda está lá
                if patch_content : # Se o patch foi analisado e não deu match
                    print(f"  Migração não confirmada explicitamente no patch para {filename} E patch existia. Não usando fallback.")
                else: # Se não tinha patch E o método antigo ainda existe
                    print(f"  Migração não confirmada (SEM patch, método antigo ainda presente ou outra razão) para {filename}.")
                # continue # Se não migrou no patch, não comenta. Adicione continue se quiser ser estrito com o patch.

        if migrated_in_patch:
            print(f"  INFO: Preparando para comentar sobre {filename}.") # Adicionado print
            formatted_comment = COMMENT_MESSAGE_TEMPLATE.format(class_name=class_name, file_path=filename)
            # ... resto da lógica de comentário ...
        else: # Adicionado para clareza
             print(f"  INFO: Nenhuma migração qualificada para comentário encontrada para {filename} após análise de patch/fallback.")


def main():
    pr_obj, repo_obj_pygithub, token = get_github_pr_details()

    print(f"Analisando PR #{pr_obj.number} no repositório {repo_obj_pygithub.full_name}")

    files_from_api = get_pr_files_via_api(repo_obj_pygithub.full_name, pr_obj.number, token)

    existing_comments = pr_obj.get_issue_comments()
    existing_comments_bodies = [comment.body for comment in existing_comments]

    check_base_async_action_migration(pr_obj, repo_obj_pygithub, files_from_api, existing_comments_bodies)

    print("Verificação de migração de BaseAsyncAction concluída.")
    sys.exit(0)

if __name__ == "__main__":
    main()