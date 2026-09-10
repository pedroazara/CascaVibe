# CascaVibe — laboratório de movimento

## Etapa atual: acelerômetro + Wi-Fi

O novo firmware está em `firmware/cascavibe_wifi/cascavibe_wifi.ino`. Ele coleta somente aceleração, oferece uma página simples para configurar Wi-Fi e envia lotes a uma API configurável. Abra `firmware/cascavibe_wifi/README.md` para gravar e conectar. O guia para você implementar sua API está em `docs/API.md`.

O protótipo de horizonte abaixo permanece como experimento anterior e usa um firmware/protocolo diferente.

Painel Python/Pygame para ESP32 + **MPU-6050** (módulo frequentemente chamado GY-521; aqui identificado como KY-521). Inclui horizonte artificial, bússola relativa, acelerômetro X/Y/Z, histórico de aceleração, giroscópio e temperatura do chip.

**Atualização necessária:** regrave o sketch do ESP32 para receber os novos instrumentos. O firmware anterior envia apenas `ANGLE,roll,pitch`; o painel ainda o aceita, mas exibe os campos ausentes como `—` e pede a atualização.

## Ligações

O esquema elétrico editável está em `hardware/kicad/CascaVibe.kicad_pro` (KiCad 9+), com biblioteca local e relatório ERC. Ele representa a montagem da placa ESP32-WROOM-32 USB com o módulo MPU-6050 pelos nomes dos pinos. Consulte `hardware/kicad/README.md` para os componentes opcionais e instruções de montagem.

| KY-521 | ESP32 |
| --- | --- |
| VCC | 3V3 |
| GND | GND |
| SDA | GPIO 21 |
| SCL | GPIO 22 |
| INT | Não conectar |
| AD0 | GND para 0x68; 3V3 para 0x69 |

> Use 3,3 V no VCC para que os níveis I²C sejam seguros para o ESP32.

## Programa do ESP32

1. Na Arduino IDE, abra **Ferramentas → Gerenciar Bibliotecas** e instale: **Adafruit MPU6050**, **Adafruit Unified Sensor** e **Adafruit BusIO**.
2. Abra `esp32_ky521/esp32_ky521.ino`.
3. Selecione sua placa ESP32 e a porta USB correta; então envie o programa.
4. Mantenha o sensor parado por aproximadamente 2 segundos ao ligar: o programa estima o offset do giroscópio. Se detectar movimento, repete a calibração.
5. No Monitor Serial a 115200 baud, você deverá ver linhas iniciadas por `IMU,`.

O programa usa a biblioteca Adafruit e um filtro complementar com tempo de amostragem medido. As faixas são ±2 g e ±250 °/s, com filtro de 21 Hz e transmissão aproximada de 50 Hz. Os pinos 21/22 são para o ESP32 clássico: ajuste as constantes se sua placa tiver outra pinagem.

## Horizonte no computador

O ambiente virtual `.venv` já está criado na raiz do projeto. Para usá-lo no terminal:

```bash
cd /home/pedro-henrique/Documentos/projetos/CascaVibe
source .venv/bin/activate
```

Caso seja necessário recriá-lo em outra máquina:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r python_horizonte/requirements.txt
```

Feche o Monitor Serial da Arduino IDE (uma porta serial só pode ser aberta por um programa) e execute, com o ambiente ativado:

```bash
# Linux
python python_horizonte/horizonte_artificial.py --port /dev/ttyACM0

# Windows (exemplo)
python python_horizonte/horizonte_artificial.py --port COM5
```

No computador atual, o ESP32 foi identificado como `/dev/ttyACM0`. Em outra máquina, a porta pode ser `/dev/ttyUSB0`, `/dev/ttyACM0` ou uma porta `COM` no Windows.

Se houver apenas uma porta serial USB, é possível omitir `--port`. Para listar portas ou abrir a demonstração:

```bash
python python_horizonte/horizonte_artificial.py --list-ports
python python_horizonte/horizonte_artificial.py --demo
```

O modo demonstração usa dados sintéticos e fica claramente identificado no cabeçalho. A janela pode ser redimensionada e mantém as proporções.

## Instrumentos e controles

| Instrumento | Como interpretar |
| --- | --- |
| Horizonte | Roll e pitch em graus. Preserva a inversão visual de roll escolhida para a montagem atual. |
| Acelerômetro | X/Y/Z no referencial físico do sensor, em m/s² e g. Inclui gravidade: parado, a resultante deve ficar perto de 1 g. Não é aceleração linear com gravidade removida. |
| Histórico | Últimos 10 segundos dos três eixos; escala vertical automática. Lacunas de recepção interrompem a curva. |
| Bússola relativa | Rumo integrado do giroscópio. `N*` indica a referência escolhida ao zerar; não indica o norte geográfico ou magnético. |
| Giroscópio | Velocidades angulares X/Y/Z em °/s, com offset inicial subtraído. |
| Temperatura | Temperatura interna do chip, não um termômetro ambiente. |
| Recepção / pacote | Taxa observada no computador e tempo desde a última amostra válida. |

- **Z** ou **Zerar referência**: define o rumo atual como 0°.
- **R** ou **Reconectar USB**: reabre a porta. Se o número da porta mudar, reinicie com o novo `--port`.
- **Esc**: fecha o painel e libera a serial.

Após 1,5 segundo sem amostra válida, o painel mostra **SEM DADOS ATUAIS**, preservando a última leitura com aviso. Uma porta aberta sozinha não conta como telemetria ativa.

## Limites da orientação

O MPU-6050 possui acelerômetro e giroscópio, mas não magnetômetro. Por isso, o rumo relativo acumula deriva mesmo após a calibração inicial. Zerar a referência não calibra o norte. Para norte magnético real será necessário adicionar um magnetômetro.

O rumo usa as velocidades angulares e a inclinação estimada. Com pitch próximo da vertical (módulo de pitch ≥80°), a integração do rumo é suspensa e o painel indica **RUMO LIMITADO**. Este filtro é voltado a movimentos de bancada; acelerações fortes, vibração, movimentos invertidos e saturação prejudicam a estimativa. O acelerômetro e o giroscópio mantêm os sinais físicos dos eixos, enquanto o roll do horizonte tem a inversão visual solicitada.

## Protocolo serial

Uma linha por amostra, separada por vírgulas e terminada por quebra de linha:

```text
IMU,ms,roll,pitch,yaw,ax,ay,az,gx,gy,gz,temp
IMU,1020,12.50,-3.25,359.99,0.100,0.200,9.800,1.000,2.000,3.000,28.50
```

`ms`: tempo desde o boot; ângulos em graus; aceleração em m/s²; giro em °/s; temperatura em °C. Linhas `INFO,` e `ERROR,` são mensagens de estado. Pacotes fragmentados são remontados, e linhas inválidas ou excessivamente longas são descartadas.

## Verificação sem hardware

```bash
python -m unittest discover -s python_horizonte -p 'test_*.py' -v
```

Os testes cobrem formato/unidades, firmware antigo, números inválidos, remontagem de pacotes, perda de sinal, reset de referência, desenho e comunicação por serial virtual (POSIX). Não substituem o teste físico após a gravação.

Referência: [guia oficial Adafruit MPU-6050](https://learn.adafruit.com/mpu6050-6-dof-accelerometer-and-gyro/arduino), incluindo as unidades retornadas por `getEvent`.
