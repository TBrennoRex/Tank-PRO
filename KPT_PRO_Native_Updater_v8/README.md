KPT PRO Native Updater v8

Updater independente para recuperação/atualização do KPT PRO por USB-MIDI/SysEx.

Alterações desta versão:
- corrige o envio SysEx WinRT: `CryptographicBuffer.create_from_byte_array()` recebe `bytes`, não `list[int]`;
- mantém a enumeração Windows.Devices.Midi/Enumeration da v7;
- aceita somente firmwares KPT PRO V9 e V14 nesta primeira fase;
- rejeita firmwares Tank/híbridos e versões KPT não autorizadas antes de qualquer comunicação;
- instala explicitamente Foundation, Foundation.Collections e Foundation.Metadata;
- usa as sobrecargas PyWinRT nomeadas find_all_async_aqs_filter* em vez de depender de find_all_async genérico;
- não inicia flash automaticamente.

## Uso seguro

1. Preserve uma cópia intacta de `KPTPRO_009.fwsc`.
2. Execute `run_updater.bat` no Windows 11.
3. Use primeiro **Procurar KPT PRO**; o teste inicial deve ser somente handshake.
4. Para recuperação, selecione um `.fwsc` KPT PRO V9 ou V14 conhecido.
5. Não desconecte USB nem interrompa a energia durante uma atualização real.

O transporte WinRT ainda precisa de validação com a pedaleira física; os testes incluídos são offline.
