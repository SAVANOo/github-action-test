import os
import re
import sys
import requests
from github import Github

BASE_ASYNC_ACTION_EXTENSION_REGEX = re.compile(r"class\s+([\w\d_]+)\s+extends\s+BaseAsyncAction")
APPLY_BASE_MAPPING_REGEX = re.compile(r"\bapplyBaseMappings\b")
APPLY_BASE_MAPPING_WITH_INDEX_REGEX = re.compile(r"\bapplyBaseMappingsWithIndex\b")

ALERT_MESSAGE = "Ao migrar para o mapping que possui índices, é necessário garantir que os índices necessários estejam pré-criados no banco de dados para evitar problemas de locks durante o deploy:"

COMMENT_MESSAGE_TEMPLATE = """
> [!WARNING]
> A fila `{class_name}` substituiu o mapeamento padrão de `applyBaseMapping()` por `applyBaseMappingWithIndex()`.
>
> `${ALERT_MESSAGE}`

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
        sys.exit(1)

    try:
        pr_number_str = github_ref.split('/')[-2]
        pr_number = int(pr_number_str)
    except (IndexError, ValueError):
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

        current_content = get_file_content_from_repo(repo_obj_pygithub, filename, pr_obj.head.sha)
        previous_content = get_file_content_from_repo(repo_obj_pygithub, filename, pr_obj.base.sha)

        if not current_content or not previous_content:
            continue

        class_name = get_class_name_if_extends_base_async(current_content)
        if not class_name:
            continue

        uses_with_index_now = bool(APPLY_BASE_MAPPING_WITH_INDEX_REGEX.search(current_content))
        used_simple_before = bool(APPLY_BASE_MAPPING_REGEX.search(previous_content))

        if not (uses_with_index_now and used_simple_before):
            continue

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

            if removed_simple_in_patch and added_with_index_in_patch:
                migrated_in_patch = True

        if not migrated_in_patch:
            if not APPLY_BASE_MAPPING_REGEX.search(current_content):
                 migrated_in_patch = True

        if migrated_in_patch:
            try:
                formatted_comment = COMMENT_MESSAGE_TEMPLATE.format(class_name=class_name)

            except Exception as e_format:
                continue

            found_identifier_in_existing_comments = False
            if existing_comments_bodies:
                for i, c_body in enumerate(existing_comments_bodies):
                    is_present = ALERT_MESSAGE in c_body
                    cleaned_c_body_part_check = c_body[:100].replace('\n', ' ')
                    if is_present:
                        found_identifier_in_existing_comments = True

            already_commented = any(ALERT_MESSAGE in c_body for c_body in existing_comments_bodies)

            if not already_commented:
                files_requiring_comment.append(formatted_comment)

    return files_requiring_comment

def main():
    pr_obj, repo_obj_pygithub, token = get_github_pr_details()

    files_from_api = get_pr_files_via_api(repo_obj_pygithub.full_name, pr_obj.number, token)

    existing_comments = pr_obj.get_issue_comments()
    existing_comments_bodies = [comment.body for comment in existing_comments]

    if existing_comments_bodies:
        for i, c_body in enumerate(existing_comments_bodies):
            cleaned_c_body_part = c_body[:150].replace('\n', ' ')

    comments_to_post = check_base_async_action_migration(pr_obj, repo_obj_pygithub, files_from_api, existing_comments_bodies)

    if comments_to_post:
        for comment_body in comments_to_post:
            pr_obj.create_issue_comment(comment_body)

    sys.exit(0)

if __name__ == "__main__":
    main()