import os
import re
import sys
import requests
from github import Github

COMMENT_MESSAGE_TEMPLATE_NEW_ACTION = """
> [!WARNING]
> ### Nova AsyncAction criada!
>
> Um novo arquivo terminando com `AsyncAction.groovy` foi adicionado neste Pull Request.
>
> As AsyncAction não nascem com índices automaticamente, então é recomendado a criação via DBA dos índices para melhorar o desempenho nas consultas e evitar locks muito longos nas filas.
>
> Exemplo:
> ```sql
> ALTER TABLE queues.sua_nova_fila_async_actoin ADD INDEX status_action_data_hash_idx (status, action_data_hash) ALGORITHM = INPLACE, LOCK = NONE;
>```
>
> Obs: Não esqueça da convenção de nomenclatura para criação de índices documentada no [livro de elite](https://github.com/asaasdev/livro-de-elite/blob/3b5048d787332b170fe0403c70a6d1b65055b3c0/processes/asaas.md?plain=1#L818).
"""


def get_github_pr_details():
    token = os.getenv('GITHUB_TOKEN')
    repository_name = os.getenv('GITHUB_REPOSITORY')
    github_ref = os.getenv('GITHUB_REF')

    if not all([token, repository_name, github_ref]):
        sys.exit(1)

    try:
        pr_number_str = github_ref.split('/')[-2]
        pr_number = int(pr_number_str)
    except (IndexError, ValueError):
        sys.exit(0)

    g = Github(token)
    repo_obj_pygithub = g.get_repo(repository_name)
    pr_obj = repo_obj_pygithub.get_pull(pr_number)
    return pr_obj, token

def get_pr_files_via_api(repository_name, pr_number, token):
    url = f"https://api.github.com/repos/{repository_name}/pulls/{pr_number}/files"
    headers = {'Authorization': f'token {token}', 'Accept': 'application/vnd.github.v3+json'}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()

def main():
    pr_obj, token = get_github_pr_details()
    repository_name = os.getenv('GITHUB_REPOSITORY')

    files_from_api = get_pr_files_via_api(repository_name, pr_obj.number, token)

    existing_comments = pr_obj.get_issue_comments()
    existing_comments_bodies = [comment.body for comment in existing_comments]

    comments_to_post_bodies = []

    for file_data in files_from_api:
        filename = file_data['filename']
        status = file_data['status']

        if status == 'added' and filename.endswith('AsyncAction.groovy'):
            already_commented_for_this_file = any(COMMENT_MESSAGE_TEMPLATE_NEW_ACTION in c_body for c_body in existing_comments_bodies)

            if not already_commented_for_this_file:
                comments_to_post_bodies.append(COMMENT_MESSAGE_TEMPLATE_NEW_ACTION)

    if comments_to_post_bodies:
        for body in comments_to_post_bodies:
            try:
                pr_obj.create_issue_comment(body)
            except Exception as e:
                print(f"ERRO: Falha ao postar comentário: {e}")

    sys.exit(0)

if __name__ == "__main__":
    main()