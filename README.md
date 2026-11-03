# Super Light Pomodoro

Pomodoro em Python 3.9+ para o terminal. Usa apenas a biblioteca padrão: sem instalação de pacotes, servidor ou processo em segundo plano. Durante a contagem, atualiza a tela uma vez por segundo.

## Usar

Na pasta do projeto:

```sh
python3 pomodoro.py             # Mostra o status; não inicia nada
python3 pomodoro.py iniciar     # Inicia apenas a próxima etapa
python3 pomodoro.py pular       # Avança uma etapa sem iniciar a próxima
python3 pomodoro.py status      # Consulta a próxima etapa e os focos concluídos
python3 pomodoro.py reiniciar   # Zera o progresso; não inicia nada
python3 pomodoro.py --help
```

Também aceita comandos em inglês: `start` equivale a `iniciar`, `skip` a `pular`, `reset` a `reiniciar` e `--state` a `--estado`. `status` e `--config` são iguais nos dois idiomas. Os comandos funcionam nos dois idiomas independentemente do idioma das mensagens.

```sh
python3 pomodoro.py start
python3 pomodoro.py status --state /caminho/progresso.json
python3 pomodoro.py reset
```

O ciclo padrão é **foco de 25 min → pausa de 5 min → foco…**. Depois do quarto foco, a pausa será de 15 min. Ao concluir a pausa longa, começa um novo ciclo.

**Cada etapa exige um novo `iniciar`, inclusive as pausas.** Ao terminar, o programa alerta e encerra. Não fica executando enquanto aguarda seu próximo comando.

Durante a contagem, uma barra e um indicador animado mostram o tempo restante. Ao redirecionar a saída para um arquivo, a animação é desativada. `Ctrl+C` cancela a sessão sem contar um foco ou avançar o ciclo; o próximo `iniciar` repete a etapa desde o começo.

Para controlar a contagem, digite no mesmo terminal e pressione **Enter**:

- `pause` ou `pausar`: congela o tempo restante.
- `resume`, `retomar` ou `continuar`: retoma de onde parou.
- `p`: alterna entre pausar e retomar.
- `skip` ou `pular`: encerra a contagem, mesmo pausada, e avança exatamente uma etapa.

O tempo pausado não conta como foco nem como pausa do ciclo. O processo permanece aberto enquanto estiver pausado, e `Ctrl+C` continua cancelando a etapa. Esses comandos funcionam durante a contagem com a entrada conectada a um terminal. `skip`/`pular` também funciona como subcomando de `python3 pomodoro.py`; os comandos de pausa e retomada são exclusivos da contagem.

Ao pular, a próxima etapa aguarda um novo `iniciar`; nenhum alerta de conclusão é emitido. O progresso avança como em uma conclusão normal: pular um foco incrementa os contadores de foco e respeita o intervalo até a pausa longa; pular a pausa longa reinicia o ciclo. Com uma contagem aberta, use `skip`/`pular` no próprio terminal dela, pois a trava bloqueia alterações por outro processo.

## Histórico diário

O arquivo `pomodoro-history.json` é criado automaticamente ao concluir a primeira etapa, na mesma pasta do arquivo de progresso. Cada data guarda os totais daquele dia:

```json
{
  "2026-11-03": {
    "ciclos_concluidos": 4,
    "foco_minutos": 100,
    "pausa_minutos": 30
  }
}
```

Cada foco concluído conta como um ciclo. Os minutos somam as durações configuradas das etapas concluídas; pausas curtas e longas entram em `pausa_minutos`. Etapas puladas ou canceladas e o tempo com o timer pausado não entram no histórico. Uma etapa que atravessa a meia-noite entra na data local em que termina.

Os dias anteriores são preservados, inclusive ao usar `reiniciar`/`reset`. Com `--estado`/`--state`, o histórico fica na pasta do progresso escolhido; arquivos de progresso na mesma pasta compartilham o histórico. Não há dependências adicionais.

## Configurar

Edite [pomodoro.json](pomodoro.json):

```json
{
  "idioma": "pt",
  "foco_minutos": 25,
  "pausa_curta_minutos": 5,
  "pausa_longa_minutos": 15,
  "pomodoros_ate_pausa_longa": 4,
  "alerta_sonoro": true,
  "notificacao_desktop": true
}
```

Use `"idioma": "pt"` para português (padrão) ou `"idioma": "en"` para inglês. A opção muda as mensagens do terminal, a ajuda (`--help`) e as notificações. As chaves do JSON permanecem iguais. Por exemplo, para consultar a ajuda no idioma de outra configuração: `python3 pomodoro.py --config /caminho/config.json --help`.

Se o JSON não puder ser lido ou o idioma for inválido, o erro aparece em português. A ajuda continua disponível e `reiniciar`/`reset` ainda permite recuperar o progresso mesmo com a configuração inválida.

Os tempos são em minutos e aceitam decimais: `0.1` equivale a 6 segundos. Cada duração deve ser maior que zero e no máximo 1440 minutos. O intervalo até a pausa longa deve ser um inteiro positivo. Propriedades omitidas usam os valores padrão. As alterações são lidas no próximo comando; uma contagem já iniciada mantém a duração original.

O progresso fica em `.pomodoro-state.json`, ao lado do script, e sobrevive ao fechamento do terminal. Não precisa editar esse arquivo. Uma trava impede duas contagens simultâneas no mesmo arquivo de progresso.

Para usar outra configuração e manter um ciclo separado:

```sh
python3 pomodoro.py iniciar --config /caminho/config.json --estado /caminho/progresso.json
```

Os arquivos JSON de configuração devem existir; as pastas dos arquivos de progresso também. Sem opções, os caminhos são relativos à localização do script, mesmo que você execute o comando a partir de outra pasta.

## Alerta ao terminar

- **macOS:** uma notificação do sistema com som quando ambas as opções estão ativadas. Usa o `osascript` que acompanha o macOS.
- **Linux:** notificação se `notify-send` estiver disponível, além do sino do terminal se o som estiver ativado.
- **Outros sistemas ou falha no comando de notificação:** sino do terminal se o som estiver ativado.

A mensagem de conclusão sempre aparece no terminal. Notificações e sons dependem das permissões, do modo Não Perturbe e das preferências do terminal/sistema. No macOS, um comando de notificação aceito pelo sistema não garante que o aviso será exibido ou ouvido. Se necessário, ajuste as notificações do aplicativo responsável pelo aviso nos Ajustes do Sistema. Para usar apenas o sino do terminal, configure `notificacao_desktop` como `false`.

## Atalho opcional

Para digitar menos, adicione ao seu `~/.zshrc` (substitua `/caminho/para/o/projeto` pelo caminho da sua instalação):

```sh
function pomo() {
  python3 "/caminho/para/o/projeto/pomodoro.py" "$@"
}
```

Depois de abrir outro terminal, use `pomo iniciar`, `pomo status` e `pomo reiniciar`.

## Verificar

```sh
python3 -m unittest discover -s tests -v
```
