# tab5-app-files

Aplicativo Gerenciador e Explorador de Arquivos para o sistema operacional **Tab5 OS** (M5Stack Tab5 / ESP32-P4), desacoplado e compilado para execução isolada em WebAssembly (WAMR).

## Características

- Navegação por diretórios do cartão microSD (`/sdcard`)
- Listagem detalhada com indicação de diretórios, tamanho e data de modificação
- Suporte a exibição/ocultação de arquivos e diretórios ocultos (iniciados com `.`)
- Interface responsiva com suporte a temas e rotação de tela
- Totalmente desacoplado do kernel do sistema operacional

## Como Compilar e Gerar o Pacote

```bash
# Executa o script de empacotamento
./tools/build.sh
```

O pacote resultante `com.tab5.files.tab5pkg` será criado na pasta `dist/` e pode ser instalado diretamente no dispositivo via Cartão SD (`/sdcard/apps/`) ou embutido na partição de sistema do Tab5 OS.

## Licença

MIT License.
