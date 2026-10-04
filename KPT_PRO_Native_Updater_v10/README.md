KPT PRO Native Updater v10

Updater independente para recuperação/atualização do KPT PRO por USB-MIDI/SysEx.

Alterações desta versão:
- corrige o envio SysEx WinRT: `CryptographicBuffer.create_from_byte_array()` recebe `bytes`, não `list[int]`;
- instala explicitamente `winrt-Windows.Storage.Streams`, necessário para o buffer MIDI WinRT;
- mantém a enumeração Windows.Devices.Midi/Enumeration da v7;
- aceita somente firmwares KPT PRO V9 e V14 nesta primeira fase;
- rejeita firmwares Tank/híbridos e versões KPT não autorizadas antes de qualquer comunicação;
- instala explicitamente Foundation, Foundation.Collections e Foundation.Metadata;
- usa as sobrecargas PyWinRT nomeadas find_all_async_aqs_filter* em vez de depender de find_all_async genérico;
- adiciona **Recuperação V9**, que seleciona o par USB-MIDI sem exigir identidade normal e só envia o `KPTPRO_009.fwsc` após confirmação;
- não inicia flash automaticamente.

## Uso seguro

1. Preserve uma cópia intacta de `KPTPRO_009.fwsc`.
2. Execute `run_updater.bat` no Windows 11.
3. Use primeiro **Procurar KPT PRO**; o teste inicial deve ser somente handshake.
4. Para uma unidade sem display, selecione `KPTPRO_009.fwsc` e use **Recuperação V9**.
5. A recuperação envia `F0 22 24 35 7F F7` somente depois da confirmação visual.
6. Não desconecte USB nem interrompa a energia durante uma atualização real.

Se o log mostrar `No module named 'winrt.windows.storage'`, execute novamente o
`run_updater.bat` desta versão para instalar a dependência correta. O teste de
descoberta/handshake não grava firmware.

O transporte WinRT ainda precisa de validação com a pedaleira física; os testes incluídos são offline.
