# Diagnóstico manual: abertura de arquivos

## Pré-condições

- Para um build independente, instalar `dist/com.tab5.files.tab5pkg` em um Tab5 OS
  com o SDK/runtime disponível. Em um build do Tab5 OS que inclua este app, o
  app Arquivos já é instalado/carregado como app embutido; não instale o
  `.tab5pkg` manualmente.
- Usar um diretório de teste contendo pelo menos um subdiretório e um arquivo
  suportado pelo host.
- Abrir o console/log do sistema e filtrar por `tab5_files`.

## Cenários

1. **Diretório não abre associação** — toque no subdiretório. Deve navegar para
   o diretório; não deve haver chamada/efeito de `tab5_file_assoc_open`.
2. **Arquivo abre associação** — volte, toque no arquivo. Deve haver uma
   tentativa de `tab5_file_assoc_open` com o caminho completo.
3. **Manifesto sem associações** — confira no pacote que
   `manifest.json.file_associations` é `[]`; o app não deve registrar handlers
   de extensão.
4. **Logs** — registre a sequência de eventos antes da tentativa, depois do
   retorno e em erro. A assinatura e o retorno de `tab5_file_assoc_open` são
   conhecidos no SDK, e o código disponível já emite logs antes/depois da
   chamada e em caso de erro.

## Resultado conhecido desta revisão

Os cenários 1–3 têm checagens estáticas automatizadas. Não há harness para
executar eventos UI ou interceptar APIs do host. O cenário de logs também é
validado automaticamente; as 4 checagens passam.
