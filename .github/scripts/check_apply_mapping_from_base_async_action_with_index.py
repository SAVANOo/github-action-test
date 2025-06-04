import os
import re
import sys
import requests
from github import Github
# import traceback # Descomente se precisar de rastreamento de pilha completo em erros

BASE_ASYNC_ACTION_EXTENSION_REGEX = re.compile(r"class\s+([\w\d_]+)\s+extends\s+BaseAsyncAction")
APPLY_BASE_MAPPING_REGEX = re.compile(r"\bapplyBaseMappings\b")
APPLY_BASE_MAPPING_WITH_INDEX_REGEX = re.compile(r"\bapplyBaseMappingsWithIndex\b")

COMMENT_MESSAGE_TEMPLATE = """
> [!WARNING]
> {identifier_alert_message}
>
> Ao migrar para o mapping que possui índices, é necessário garantir que os índices necessários estejam pré-criados no banco de dados para evitar problemas de locks durante o deploy:

- [ ] Inserir o índice em banco:
```sql
ALTER TABLE queues.sua_fila_async_action ADD INDEX "action_data_hash_status_idx" (action_data_hash, status) ALGORITHM = INSTANT, LOCK = NONE;
```

- [ ] Validar se o índice foi criado corretamente (action_data_hash_status_idx):
```sql
SHOW INDEX FROM queues.sua_fila_async_action;
```



"""

def get_github_pr_details():
    token = os.getenv('GITHUB_TOKEN')
    repository_name = os.getenv('GITHUB_REPOSITORY')
    github_ref = os.getenv('GITHUB_REF')

    if not all([token, repository_name, github_ref]):
        print("Erro: Variáveis de ambiente GITHUB_TOKEN, GITHUB_REPOSITORY ou GITHUB_REF não definidas.", flush=True)
        sys.exit(1)

    try:
        pr_number_str = github_ref.split('/')[-2]
        pr_number = int(pr_number_str)
    except (IndexError, ValueError):
        print(f"Erro: Não foi possível extrair o número do PR de GITHUB_REF: {github_ref}", flush=True)
        print("Este script foi projetado para rodar em eventos de pull_request.", flush=True)
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
        print(f"DEBUG GET_CONTENT: Tentando obter conteúdo de {file_path} no SHA {sha}", flush=True)
        content_file = repo_obj_pygithub.get_contents(file_path, ref=sha)
        decoded_content = content_file.decoded_content.decode('utf-8')
        print(f"DEBUG GET_CONTENT: Conteúdo de {file_path} obtido com sucesso.", flush=True)
        return decoded_content
    except Exception as e:
        print(f"ERRO GRAVE GET_CONTENT: Não foi possível obter conteúdo do arquivo {file_path} no SHA {sha}. Erro: {e}", flush=True)
        # traceback.print_exc(file=sys.stdout)
        # sys.stdout.flush()
        return None

def get_class_name_if_extends_base_async(file_content):
    match = BASE_ASYNC_ACTION_EXTENSION_REGEX.search(file_content)
    return match.group(1) if match else None

def check_base_async_action_migration(pr_obj, repo_obj_pygithub, files_from_api, existing_comments_bodies):
    print("DEBUG CHECK_FUNC: Entrou em check_base_async_action_migration", flush=True)
    files_requiring_comment = []
    print(f"DEBUG CHECK_FUNC: Número de arquivos da API para verificar: {len(files_from_api)}", flush=True)

    for file_data_from_api in files_from_api:
        print("DEBUG CHECK_FUNC_LOOP: Dentro do loop de arquivos da API", flush=True)
        filename = file_data_from_api['filename']
        status = file_data_from_api['status']
        patch_content = file_data_from_api.get('patch')

        if not filename.endswith('.groovy') or status != 'modified':
            print(f"DEBUG CHECK_FUNC_LOOP: Arquivo {filename} (status: {status}) não é .groovy modificado. Pulando.", flush=True)
            continue

        print(f"Verificando arquivo modificado: {filename}", flush=True)

        current_content = get_file_content_from_repo(repo_obj_pygithub, filename, pr_obj.head.sha)
        previous_content = get_file_content_from_repo(repo_obj_pygithub, filename, pr_obj.base.sha)

        if not current_content or not previous_content:
            print(f"  Conteúdo atual ou anterior de {filename} não encontrado. Pulando.", flush=True)
            continue

        class_name = get_class_name_if_extends_base_async(current_content)
        if not class_name:
            print(f"  Arquivo {filename} não estende BaseAsyncAction ou nome da classe não encontrado. Pulando.", flush=True)
            continue
        print(f"  Arquivo {filename} estende BaseAsyncAction (Classe: {class_name}).", flush=True)

        uses_with_index_now = bool(APPLY_BASE_MAPPING_WITH_INDEX_REGEX.search(current_content))
        used_simple_before = bool(APPLY_BASE_MAPPING_REGEX.search(previous_content))

        print(f"    DEBUG LOGIC: current_content contém '{APPLY_BASE_MAPPING_WITH_INDEX_REGEX.pattern}'? {uses_with_index_now}", flush=True)
        print(f"    DEBUG LOGIC: previous_content contém '{APPLY_BASE_MAPPING_REGEX.pattern}'? {used_simple_before}", flush=True)

        if not (uses_with_index_now and used_simple_before):
            print(f"    DEBUG LOGIC: Condição (uses_with_index_now AND used_simple_before) NÃO atendida. Pulando.", flush=True)
            continue

        print(f"  Arquivo {filename}: USA 'WithIndex' agora E USAVA 'Simple' antes.", flush=True)

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
            print(f"      DEBUG PATCH: Analisando patch para {filename}:", flush=True)
            print(f"      DEBUG PATCH: Linha removida com '{APPLY_BASE_MAPPING_REGEX.pattern}' encontrada? {removed_simple_in_patch}", flush=True)
            print(f"      DEBUG PATCH: Linha adicionada com '{APPLY_BASE_MAPPING_WITH_INDEX_REGEX.pattern}' encontrada? {added_with_index_in_patch}", flush=True)

            if removed_simple_in_patch and added_with_index_in_patch:
                migrated_in_patch = True
                print(f"  Detectada migração explícita no patch de {filename}.", flush=True)
            else:
                print(f"  Migração NÃO detectada explicitamente no patch (removed_simple={removed_simple_in_patch}, added_with_index={added_with_index_in_patch}).", flush=True)
        else:
            print(f"  AVISO: patch_content não encontrado para {filename}.", flush=True)


        if not migrated_in_patch:
            if not APPLY_BASE_MAPPING_REGEX.search(current_content):
                 print(f"  INFO FALLBACK: Método antigo '{APPLY_BASE_MAPPING_REGEX.pattern}' não encontrado no conteúdo ATUAL de {filename}. Considerando migração por fallback.", flush=True)
                 migrated_in_patch = True
            else:
                print(f"  INFO: Migração não confirmada (patch ou fallback falhou) para {filename}.", flush=True)


        if migrated_in_patch:
            print(f"  INFO: Preparando para comentar sobre {filename}.", flush=True)

            try:
                identifier_alert_message = f"A fila `{class_name}` substituiu o mapeamento padrão de `applyBaseMapping()` por `applyBaseMappingWithIndex()`."
                formatted_comment = COMMENT_MESSAGE_TEMPLATE.format(identifier_alert_message=identifier_alert_message)

            except Exception as e_format:
                print(f"    ERRO CRÍTICO durante formatação/criação de string: {e_format}", flush=True)
                continue

            print(f"    DEBUG CHECKPOINT 6: Antes de checar comentários existentes", flush=True)
            print(f"    DEBUG COMMENTS_EXIST: Checando {len(existing_comments_bodies)} comentários existentes:", flush=True)

            found_identifier_in_existing_comments = False
            if existing_comments_bodies:
                for i, c_body in enumerate(existing_comments_bodies):
                    is_present = identifier_alert_message in c_body
                    cleaned_c_body_part_check = c_body[:100].replace('\n', ' ') # CORREÇÃO APLICADA
                    print(f"      DEBUG COMMENTS_EXIST: Comentário #{i} (início): '{cleaned_c_body_part_check}...' ({'IDENTIFIER ENCONTRADO' if is_present else 'não encontrado'})", flush=True)
                    if is_present:
                        found_identifier_in_existing_comments = True
            else:
                print("      DEBUG COMMENTS_EXIST: Nenhum comentário existente para checar.", flush=True)


            print("    DEBUG CHECKPOINT 7: Antes de 'any(identifier_alert_message in c_body)'", flush=True)
            already_commented = any(identifier_alert_message in c_body for c_body in existing_comments_bodies)
            print(f"    DEBUG CHECKPOINT 8: DEPOIS de 'any', 'already_commented' é {already_commented}", flush=True)
            if found_identifier_in_existing_comments != already_commented:
                 print(f"    DEBUG WARNING: Discrepância entre loop de debug e 'any()' para already_commented!", flush=True)


            if not already_commented:
                print(f"    INFO: Adicionando comentário para {filename} à lista de postagem.", flush=True)
                files_requiring_comment.append(formatted_comment)
            else:
                print(f"  INFO: Comentário para {filename} já existe (ou identificador encontrado). Pulando.", flush=True)
        else:
             print(f"  INFO: Nenhuma migração qualificada para comentário encontrada para {filename} após análise de patch/fallback.", flush=True)

    print(f"DEBUG CHECK_FUNC: Fim de check_base_async_action_migration. {len(files_requiring_comment)} comentários para postar.", flush=True)
    return files_requiring_comment


def main():
    print("DEBUG MAIN: Script iniciado.", flush=True)
    pr_obj, repo_obj_pygithub, token = get_github_pr_details()

    print(f"Analisando PR #{pr_obj.number} no repositório {repo_obj_pygithub.full_name}", flush=True)

    files_from_api = get_pr_files_via_api(repo_obj_pygithub.full_name, pr_obj.number, token)
    print(f"DEBUG MAIN: Número de arquivos obtidos da API: {len(files_from_api)}", flush=True)

    existing_comments = pr_obj.get_issue_comments()
    existing_comments_bodies = [comment.body for comment in existing_comments]

    print(f"DEBUG MAIN: Número total de comentários existentes no PR: {len(existing_comments_bodies)}", flush=True)
    if existing_comments_bodies:
        print("DEBUG MAIN: Listando início dos comentários existentes:", flush=True)
        for i, c_body in enumerate(existing_comments_bodies):
            cleaned_c_body_part = c_body[:150].replace('\n', ' ') # CORREÇÃO APLICADA
            print(f"  DEBUG MAIN: Comentário #{i} (início): '{cleaned_c_body_part}...'")
    else:
        print("DEBUG MAIN: Nenhum comentário existente encontrado no PR.", flush=True)

    comments_to_post = check_base_async_action_migration(pr_obj, repo_obj_pygithub, files_from_api, existing_comments_bodies)

    if comments_to_post:
        print(f"DEBUG MAIN: {len(comments_to_post)} comentários serão postados.", flush=True)
        for comment_body in comments_to_post:
            try:
                print(f"Postando comentário no PR #{pr_obj.number}...", flush=True)
                pr_obj.create_issue_comment(comment_body)
                print("  Comentário postado com sucesso.", flush=True)
            except Exception as e:
                print(f"  Erro ao postar comentário: {e}", flush=True)
    else:
        print("DEBUG MAIN: Nenhum comentário para postar.", flush=True)


    print("Verificação de migração de BaseAsyncAction concluída.", flush=True)
    sys.exit(0)

if __name__ == "__main__":
    main()