# Runbook de rollback para dados públicos

## Objetivo
Reverter o projeto para o último estado válido de dados públicos em menos de 5 minutos, sem depender de conhecimento tácito do autor.

## Quando usar
- a rotina anual/diária falhou
- um JSON crítico ficou inválido
- a publicação foi feita com dados inconsistentes
- uma fonte externa mudou e quebrou a geração
- há evidência de que a versão atual não pode ser entregue em produção

## Pré-condições
- o repositório está acessível
- o ambiente local tem Python e as dependências instaladas
- o SHA do último estado válido está disponível
- a branch de produção está identificada (normalmente `main`)

## Ferramenta principal
- `scripts/rollback_dados.py`

## Passo a passo

### 1) Identificar o SHA do último estado válido
Use o histórico do repositório para localizar o último commit em que os dados estavam válidos.

Se não souber o SHA, pode usar a lógica automática do script, que tenta localizar o último estado bom.

### 2) Executar o rollback
Opcionalmente, usando um SHA específico:

```bash
python scripts/rollback_dados.py --sha <SHA> --push --branch main
```

Ou usando a detecção automática do último válido:

```bash
python scripts/rollback_dados.py --push --branch main
```

### 3) Validar antes de concluir
O script roda a validação dos JSONs críticos automaticamente. Se qualquer schema falhar, ele aborta antes de confirmar o rollback.

Validação manual, se necessário:

```bash
python scripts/validar_jsons.py
```

### 4) Verificar a produção
- abrir o site
- conferir a página inicial
- verificar os dados principais
- confirmar que os JSONs críticos estão válidos
- checar que o conteúdo foi restaurado para o estado funcional

### 5) Registrar o evento
Registrar no log do projeto ou em uma issue do repositório:
- SHA restaurado
- motivo do rollback
- hora da reversão
- status final em produção

## Regras de segurança
- nunca publicar dados sem validação
- nunca editar arquivos gerados manualmente sem comparar com o último estado bom
- rollback de dados deve sempre validar antes de ser considerado concluído
- revertê-lo para a última versão conhecida como correta, não para o último commit disponível

## Critérios de sucesso
- os JSONs críticos passaram na validação
- o site ficou funcional novamente
- os dados em produção voltaram ao último estado válido
- o problema foi documentado para correção da causa raiz

## Observações
- o rollback é feito em dados públicos e artefatos gerados
- não é necessário reverter todo o código a menos que a própria rotina tenha sido alterada de forma incorreta
- o foco é reestabelecer a integridade dos dados e a estabilidade da publicação
