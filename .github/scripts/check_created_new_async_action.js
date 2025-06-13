module.exports = async({ github, context, core }) => {
    const COMMENT_MESSAGE = `
> [!WARNING]
> ### Nova AsyncAction criada!
>
> Um novo arquivo terminando com \`AsyncAction.groovy\` foi adicionado neste Pull Request.
>
> As AsyncAction não nascem com índices automaticamente, então é recomendado a criação via DBA dos índices para melhorar o desempenho nas consultas e evitar locks muito longos nas filas.
>
> Exemplo:
> \`\`\`sql
> ALTER TABLE queues.sua_nova_fila_async_actoin ADD INDEX status_action_data_hash_idx (status, action_data_hash) ALGORITHM = INPLACE, LOCK = NONE;
> \`\`\`
>
> Obs: Não esqueça da convenção de nomenclatura para criação de índices documentada no [livro de elite](https://github.com/asaasdev/livro-de-elite/blob/3b5048d787332b170fe0403c70a6d1b65055b3c0/processes/asaas.md?plain=1#L818).
`;

    try {
        const prNumber = context.issue.number;
        const { owner, repo } = context.repo;

        const { data: existingComments } = await github.rest.issues.listComments({
            owner,
            repo,
            issue_number: prNumber,
        });

        const alreadyCommented = existingComments.some(comment =>
            comment.body.includes("### Nova AsyncAction criada!")
        );

        if (alreadyCommented) return;

        const files = await github.paginate(github.rest.pulls.listFiles, {
            owner,
            repo,
            pull_number: prNumber
        });

        const newAsyncActionFile = files.find(file =>
            file.status === 'added' && file.filename.endsWith('AsyncAction.groovy')
        );

        if (newAsyncActionFile) {
            await github.rest.issues.createComment({
                owner,
                repo,
                issue_number: prNumber,
                body: COMMENT_MESSAGE
            });
        }
    } catch (error) {
        core.setFailed(`ERRO: Falha ao verificar a criação de AsyncAction: ${error.message}`);
    }
};