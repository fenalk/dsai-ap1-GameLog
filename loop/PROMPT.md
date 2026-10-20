# Loop: sincronização agendada do catálogo (GameLog)

## 1. Tarefa recorrente e gatilho

**Tarefa:** manter o catálogo da Steam atualizado — trazer jogos novos e corrigir os metadados
(título, sinopse, data, desenvolvedora, gêneros, plataformas e capa) dos jogos que já estão no catálogo.

**Gatilho: agenda (cron), uma vez por dia.** A Steam não emite evento quando um jogo muda ou é
lançado; a única forma de descobrir é consultar. Um evento interno (ex.: alguém avaliar um jogo)
não resolve isso, porque a mudança acontece fora da aplicação. O comando precisa ser idempotente
para que a execução diária possa repetir sem duplicar nem sobrescrever dados de usuários.

O agendador fica **fora** do processo web (cron do host ou cron job do Render chamando o CLI).
Não criar scheduler em thread/processo dentro do Flask: o deploy usa 2 workers Gunicorn e o job
rodaria duas vezes.

## 2. Objetivo desta rodada

Implementar o comando `flask sync-games` e a função que ele usa, sem rede nos testes.

Contrato obrigatório:

- `flask sync-games [--fonte steam] [--limite 40] [--termo TEXTO]`
- Saída em uma linha: `novos=N atualizados=M ignorados=I falhas=F`
- `--fonte` desconhecida: erro de uso, exit code diferente de 0 (igual a `import-games`).
- `listar()` da fonte levanta exceção antes de entregar itens: o comando imprime
  `erro=fonte indisponivel: <motivo>` e sai com exit code 1.
- Item com `{"erro": ...}`: conta `falhas`, não aborta.
- Item sem título ou sem data de lançamento válida: conta `ignorados`, não aborta.
- Jogo novo: cria com `source` e `external_id`, conta `novos`.
- Jogo existente de mesma `source` + `external_id`: atualiza os campos mutáveis e conta
  `atualizados` **apenas se algum campo mudou**; se nada mudou, conta `ignorados`.
- O `slug` de um jogo existente **nunca** muda (URLs não podem quebrar).
- Identidade é `source` + `external_id`: o mesmo `external_id` em outra fonte é outro jogo.
- Falha de banco em um item: rollback do item, conta `falhas`, continua no próximo.
- Não apagar jogos, avaliações, status, listas nem `removed_at`.
- Não criar dependência nova. Só stdlib + Flask/SQLAlchemy já instalados.

Sugestão de desenho (não obrigatória): `src/app/sync.py` com
`sincronizar(fonte, limite=40, termo=None, modulo=None) -> (novos, atualizados, ignorados, falhas)`,
e o comando em `src/app/cli.py` apenas chamando essa função. `modulo` permite injetar a fonte falsa
nos testes, como `importar()` já faz em `src/app/fontes/__init__.py`.

## 3. Critério de parada (conferível por máquina)

A rodada só passa se **todos** os comandos abaixo saírem com código 0:

```bash
cd /media/fernanda/Dados/GitHub/dsai-ap1-GameLog
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q tests/test_sync.py
```

Além disso, o porteiro confere:

1. `tests/test_sync.py` existe e coleta **no mínimo 6 testes**.
2. `git status --porcelain` só lista caminhos permitidos:
   - `src/app/sync.py` (novo)
   - `src/app/cli.py` (só o registro do comando)
   - `tests/test_sync.py` (novo)
   - `README.md` (opcional, só documentação do comando/cron)
   - `loop/` e `diario/` (do próprio exercício)
3. Nenhum arquivo proibido foi alterado (lista na seção 4).
4. Os testes novos cobrem, no mínimo:
   - criação de jogo novo e segunda execução sem duplicar;
   - atualização de metadados de jogo existente sem trocar o slug e sem duplicar;
   - item inválido, item com `erro` e item válido na mesma execução;
   - fonte fora do ar (exit code 1);
   - saída exata do comando e fonte desconhecida;
   - mesmo `external_id` em outra fonte não é o mesmo jogo.
5. Nenhum teste usa rede real, `sleep`, thread ou banco fora do `tmp_path` do pytest.

## 4. Guardrails: o que o loop NÃO pode tocar

Não editar, renomear nem apagar:

- `src/app/models.py`, `src/app/seed.py`, `src/app/util.py`
- `src/app/fontes/__init__.py` e `src/app/fontes/*`
- `src/app/dados/jogos.json`
- `src/app/auth.py`, `src/app/admin.py`, `src/app/avaliacoes.py`, `src/app/recomendacoes.py`,
  `src/app/afinidade.py`, `src/app/api.py`, `src/app/routes.py`
- `src/app/templates/**`, `src/app/static/**`
- `tests/conftest.py` e todos os `tests/test_*.py` existentes (só pode criar `tests/test_sync.py`)
- `SPEC/**`, `prompts/**`, `render.yaml`, `dev.sh`, `requirements.txt`, `pytest.ini`, `.env*`
- histórico do git: nada de `commit`, `amend`, `rebase`, `reset`, `checkout` ou `stash`

Se a solução exigir mexer em qualquer item acima, **pare e escale** em vez de editar.

## 5. Quando parar e chamar o humano

Pare imediatamente e relate, sem tentar contornar, se:

- precisar alterar arquivo proibido, modelo de dados ou contrato de fonte;
- precisar de dependência nova, serviço externo, chave ou rede nos testes;
- o comportamento esperado for ambíguo (ex.: o que conta como `atualizado`);
- um teste existente falhar por motivo aparentemente não relacionado à mudança;
- o mesmo erro persistir após uma tentativa de correção dentro da rodada;
- for a 3ª rodada e o critério de parada ainda não tiver sido atingido.

Nesses casos, entregue: o que tentou, o erro exato, os arquivos tocados e a decisão que precisa
de humano. Não faça mudanças especulativas para “ficar verde”.

## 6. Protocolo da rodada

1. Leia este arquivo inteiro e inspecione o código e os testes existentes antes de editar.
2. Faça a menor mudança completa que atenda ao contrato (sem refatorar o resto).
3. Rode os dois comandos da seção 3 e itere dentro desta mesma rodada se o erro for seu.
4. Não faça commit. Deixe as mudanças no working tree para o porteiro.
5. Responda com: arquivos alterados, comandos executados, resultado dos testes e pendências.
