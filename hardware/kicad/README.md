# CascaVibe — circuito KiCad

Abra `CascaVibe.kicad_pro` no KiCad 9 ou superior e depois `CascaVibe.kicad_sch`. A biblioteca local `CascaVibe.kicad_sym` está registrada em `sym-lib-table`; mantenha esses arquivos juntos.

Este é o **esquema elétrico editável de montagem entre módulos prontos**, correspondente ao firmware do projeto. U1 representa sua placa USB com ESP32-WROOM-32. U2 representa o módulo GY-521/KY-521 com MPU-6050. Não é um circuito para alimentar o encapsulamento WROOM-32 avulso nem um layout de placa de circuito impresso.

Os símbolos mostram apenas as interfaces utilizadas. Os identificadores dos pinos são nomes funcionais, não números de posição de headers. Use os nomes da serigrafia da placa; a marcação WROOM-32 sozinha não identifica a disposição física dos headers. Os módulos foram excluídos da transferência para PCB para evitar um encaixe físico presumido.

## Montagem

Desconecte o USB durante as ligações. Depois de conferir os fios, alimente a placa pelo próprio USB, que também faz a comunicação com o painel.

| Placa ESP32-WROOM-32 | Módulo MPU-6050 | Função |
| --- | --- | --- |
| 3V3 | VCC | Alimentação do módulo em 3,3 V |
| GND | GND | Referência comum |
| GPIO22 | SCL | Clock I²C |
| GPIO21 | SDA | Dados I²C |
| GND | AD0 | Endereço I²C 0x68 |
| — | XDA, XCL, INT | Deixar desconectados |

Não ligar SDA/SCL a 5 V. Usar fios curtos. O regulador e a interface USB já pertencem à placa de desenvolvimento. O firmware também detecta 0x69, mas este esquema fixa AD0 no GND para usar 0x68.

## Componentes

| Referência | Quantidade | Item | Montagem |
| --- | --- | --- | --- |
| U1 | 1 | Placa de desenvolvimento USB com ESP32-WROOM-32 | Obrigatória |
| U2 | 1 | Módulo GY-521/KY-521 com MPU-6050 | Obrigatória |
| — | 5 | Fios de ligação | Obrigatória |
| R1, R2 | 2 | Resistor 4,7 kΩ | DNP: opcional, somente se o módulo não tiver pull-ups I²C |
| C1 | 1 | Capacitor cerâmico 100 nF, ≥6,3 V | DNP: desacoplamento adicional opcional |
| C2 | 1 | Capacitor cerâmico 10 µF, ≥6,3 V | DNP: desacoplamento adicional opcional |

**DNP = não montar na configuração padrão.** Os riscos sobre os passivos no desenho são a indicação DNP do KiCad. Verifique os componentes do seu módulo antes de instalar pull-ups adicionais; os valores em paralelo alteram a carga do barramento. Caso necessários, R1/R2 ligam SCL/SDA a 3V3. Os capacitores adicionais ficam entre 3V3 e GND, próximos ao sensor; módulos comerciais normalmente já contêm desacoplamento.

## Verificação

`verificacao-erc.rpt` contém a verificação elétrica do KiCad. `CascaVibe.net.xml` é a lista de conexões exportada, conferida contra os quatro sinais de interligação e AD0. ERC valida o esquema lógico; não verifica a serigrafia, a montagem física ou a presença real de resistores no módulo.

```bash
kicad-cli sch erc --exit-code-violations -o verificacao-erc.rpt CascaVibe.kicad_sch
```

A bússola do painel continua sendo relativa ao giroscópio; o MPU-6050 não contém magnetômetro. Nenhum sensor adicional de bússola foi incluído neste circuito.

Referências de consulta:

- [Espressif — ESP32-DevKitC: alimentação e nomes dos sinais](https://documentation.espressif.com/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html). Referência de placa com WROOM; o desenho físico dos seus headers pode ser diferente.
- [Adafruit — MPU-6050](https://learn.adafruit.com/mpu6050-6-dof-accelerometer-and-gyro). Referência do sensor e da biblioteca, não uma garantia de pinagem de módulos de outros fabricantes.
