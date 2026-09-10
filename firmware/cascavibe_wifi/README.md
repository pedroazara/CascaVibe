# CascaVibe — firmware básico Wi-Fi

Somente aceleração XYZ do MPU-6050, configuração pelo navegador e cliente HTTP/S para a API que você vai desenvolver. O painel é simples: rede e senha na tela inicial; API e diagnóstico em seções recolhidas.

## 1. Ligações

| ESP32-WROOM-32 DevKit | GY-521/KY-521 |
| --- | --- |
| 3V3 | VCC |
| GND | GND e AD0 |
| GPIO21 | SDA |
| GPIO22 | SCL |

INT, XDA e XCL ficam desconectados. O firmware também detecta 0x69. A leitura usa FIFO consultada periodicamente e não requer um fio de interrupção nesta versão.

## 2. Gravar

1. Na Arduino IDE, instale **Adafruit MPU6050**, **Adafruit Unified Sensor**, **Adafruit BusIO** e **ArduinoJson 7**.
2. Abra a pasta deste sketch e o arquivo `cascavibe_wifi.ino`. Mantenha `panel.h`, `protocol.h` e `ack.h` junto dele.
3. Selecione sua placa ESP32 clássica — compilação de referência com **ESP32 Dev Module** e core Arduino-ESP32 **3.3.11**.
4. Selecione a porta USB e envie o sketch.
5. Abra o Monitor Serial em **115200 baud** e pressione EN/RESET para ler os dados de acesso.

Não grave o sketch antigo de `esp32_ky521` para esta etapa. Ele continua disponível como experimento de horizonte artificial; o painel Python antigo espera outro protocolo.

## 3. Conectar ao painel

O Monitor Serial mostra:

```text
Rede: CascaVibe-XXXXXX
Senha AP/painel: cascavibe
Usuario: cascavibe
Abrir http://192.168.4.1
```

No celular ou computador:

1. Conecte à rede CascaVibe com a senha **cascavibe**. Se o celular avisar que a rede não tem Internet, mantenha a conexão.
2. Abra **http://192.168.4.1** manualmente. A detecção automática de portal não é garantida.
3. Quando o navegador pedir login, use **cascavibe** tanto no usuário quanto na senha.
4. Digite o nome do Wi-Fi do local ou use **Buscar redes**. Informe a senha.
5. Clique **Salvar e conectar**. A placa salva e reinicia.
6. Reconecte à rede CascaVibe e recarregue a página para conferir se o Wi-Fi do local conectou.

Use Wi-Fi de **2,4 GHz**. O ponto de acesso protegido permanece ligado para recuperação, inclusive quando a senha da rede do local estiver errada. Assim você pode corrigir sem regravar. A página também fica acessível pelo IP da placa na rede local.

O acesso local é fixo: usuário e senha **cascavibe**, também usado como senha da rede criada pelo ESP32. Ao regravar esta versão, a senha aleatória antiga deixa de valer, sem precisar apagar as configurações. A senha do Wi-Fi do local e o token da API continuam separados e não aparecem no endpoint de status nem no log serial. Essas configurações ficam em NVS; este MVP não habilita criptografia da flash, proteção física contra leitura nem gestão de usuários. Use o painel apenas em rede confiável: a senha é compartilhada e a página local usa HTTP.

## 4. Usar antes de ter API

Deixe **URL de envio** vazia. O sensor continua coletando; em **Diagnóstico**, confira taxa observada e erros. Use **Baixar último lote** para obter um JSON de 500 amostras reais.

Sem API configurada, só o último lote completo fica disponível para download. Isso não é um gravador de sessões longas.

## 5. Quando sua API estiver pronta

Em **API (opcional)**, informe URL completa e token do dispositivo. Para HTTP de bancada, marque **Usar HTTP na rede local (sem criptografia)** e use o IP privado literal do seu computador. HTTPS exige a CA PEM na seção de certificado.

O contrato completo, o retorno esperado e os testes estão em `docs/API.md`, na raiz do projeto. Há um JSON completo de exemplo em `docs/exemplos/lote-sintetico.json`.

## 6. Coleta e limites

- Perfil inicial: **500 Hz**, **±2 g**, DLPF configurado em aproximadamente **94 Hz**.
- Lotes de **500 amostras / 1 segundo**, com XYZ brutos de 16 bits e escala no JSON.
- Apenas acelerômetro é enviado ao FIFO; gyro/temperatura/orientação não são dados de coleta. O gyro pode permanecer ativo como referência interna de clock.
- Tarefa de aquisição separada do servidor web e do envio.
- Até seis lotes em fila mais um em envio; sem persistência offline.
- Lotes novos são descartados e contados se a fila encher. Reiniciar perde pendências.
- Overflow/erro I²C descarta a janela parcial e muda o segmento; não une a lacuna silenciosamente.
- Timestamps são estimados pela FIFO e taxa nominal, com UTC nulo.
- A taxa mostrada é contagem recebida por intervalo de tempo; não é medição individual do jitter.
- O perfil ainda precisa ser validado no equipamento que escolhermos.

Salvar configurações ou reiniciar pede confirmação porque esvazia a memória. Após rejeição HTTP 4xx permanente, corrija a API/configuração e reinicie para retomar. Reenvios transitórios são automáticos.

## 7. Validação e teste físico pendente

O sketch foi compilado para ESP32-WROOM-32/ESP32 Dev Module com as bibliotecas locais. Testes C++ verificam decodificação, escala, saturação, formação de lotes, descontinuidade e confirmação de recebimento. A interface foi conferida no navegador usando dados simulados.

Após gravar na placa, verificar:

- [ ] Sensor reconhecido e taxa perto de 500 Hz.
- [ ] Um lote baixado tem 500 amostras de 3 inteiros.
- [ ] Parado, um eixo próximo de 1 g conforme orientação, sem exigir todos os eixos em zero.
- [ ] Credenciais sobrevivem ao reset e o Wi-Fi conecta.
- [ ] Senha incorreta permite corrigir pelo AP.
- [ ] Envio não causa overflow com a API em uso.
- [ ] API confirma e repetição não duplica dados.
- [ ] Queda do Wi-Fi aumenta fila/descarte de forma visível e reconexão retoma.

Esses testes de hardware não foram realizados automaticamente; compilar não comprova a taxa física nem a conectividade real.
