# CascaVibe — guia para você implementar a API

Contrato do firmware **0.2.0**, criado em 2026-09-10. A implementação da API, do acesso ao banco e do processamento Python/ML continua com você. Este guia descreve o cliente que já está no ESP32.

## 1. Comece com uma única rota

Crie `POST /api/v1/telemetry/batches` — ou outro caminho, desde que informe a URL completa no painel.

Responsabilidades da rota:

1. Autenticar o token do dispositivo.
2. Validar o JSON e a identidade do emissor.
3. Identificar o lote e detectar reenvio.
4. Persistir de forma recuperável.
5. Responder com o ACK definido abaixo.

O ESP32 não acessa o banco diretamente. Você pode usar FastAPI e PostgreSQL, mas a tecnologia é sua escolha. O contrato HTTP é o que precisa ser compatível.

## 2. Requisição enviada pelo ESP32

```http
POST /api/v1/telemetry/batches
Content-Type: application/json
Authorization: Bearer TOKEN_CONFIGURADO_NO_PAINEL
Idempotency-Key: cv-0123456789ab.0123456789abcdef0123456789abcdef.0
```

O corpo é JSON UTF-8. Cada lote tem **500 amostras XYZ**, cobrindo nominalmente 1 segundo a 500 Hz. São contagens inteiras brutas, não graus nem m/s² já convertidos.

Exemplo de estrutura (a lista está abreviada para leitura; não envie este trecho como teste):

```text
{
  "schema_version": 1,
  "device_id": "cv-0123456789ab",
  "boot_id": "0123456789abcdef0123456789abcdef",
  "batch_id": "cv-0123456789ab.0123456789abcdef0123456789abcdef.0",
  "batch_seq": 0,
  "segment_id": 1,
  "firmware_version": "0.2.0",
  "sensor": "MPU6050",
  "sample_rate_hz": 500,
  "sample_count": 500,
  "first_sample_index": 0,
  "t0_monotonic_us": 1000000,
  "timestamp_quality": "estimated_from_fifo",
  "dt_us": 2000,
  "encoding": "int16_counts",
  "accel_range_g": 2,
  "dlpf_cfg": 2,
  "scale_m_s2_per_count": 0.0005985504150390625,
  "clipped_samples": 0,
  "t0_utc": null,
  "axis_order": ["x", "y", "z"],
  "samples": [[0, 0, 16384], ... mais 499 amostras ...]
}
```

Para um JSON completo de teste, use `docs/exemplos/lote-sintetico.json`. Ele é um exemplo sintético de sensor parado, com 500 amostras; não use como dado de treinamento ou medição real.

### Campos e validação

| Campo | Regra do firmware 0.2.0 |
| --- | --- |
| `schema_version` | Inteiro 1; não misturar contratos futuros sem validação |
| `device_id` | ID da placa, formato `cv-` + 12 caracteres hexadecimais |
| `boot_id` | 32 caracteres hexadecimais; muda a cada boot |
| `batch_id` | `device_id.boot_id.batch_seq` |
| `batch_seq` | Inteiro crescente dentro do boot; começa em 0; pode ter lacunas |
| `segment_id` | Muda quando o sensor reinicia ou a continuidade é perdida |
| `first_sample_index` | Índice da primeira amostra dentro do segmento; avança 500 por lote completo |
| `sample_count` | 500 e igual ao comprimento de `samples` |
| `samples` | 500 listas com exatamente 3 inteiros entre -32768 e 32767 |
| `sample_rate_hz`, `dt_us` | 500 e 2000, nominais |
| `accel_range_g`, `dlpf_cfg` | 2 e 2; filtro configurado aproximadamente em 94 Hz |
| `encoding`, `axis_order` | `int16_counts` e `["x","y","z"]` |
| `scale_m_s2_per_count` | Aproximadamente 9,80665 / 16384; tolerar arredondamento JSON |
| `clipped_samples` | Número de amostras com qualquer eixo ≥32700 ou ≤-32700; 0 a 500 |
| `t0_monotonic_us` | Tempo estimado da primeira amostra, relativo ao boot |
| `timestamp_quality` | `estimated_from_fifo` |
| `t0_utc` | `null` nesta versão |
| `firmware_version`, `sensor` | Proveniência: 0.2.0 e MPU6050 |

Rejeite valores não finitos, tipos incompatíveis, configurações desconhecidas e lotes excessivos. Uma restrição inicial de corpo de **32 KiB** comporta os lotes atuais, que ficam abaixo de 15 KiB. Não aceite tráfego ilimitado porque há autenticação.

Valide que o token está autorizado para aquele `device_id`. Um emissor não deve conseguir trocar o ID no corpo para gravar dados como outro dispositivo.

## 3. ACK obrigatório

Depois de salvar um lote novo, responda **201 Created**:

```json
{"accepted":true,"batch_id":"cv-0123456789ab.0123456789abcdef0123456789abcdef.0"}
```

No reenvio de um lote já salvo com o mesmo conteúdo, responda **200 OK** com o mesmo corpo.

Requisitos importantes do cliente atual:

- `accepted` precisa ser o booleano `true`, não a string `"true"`.
- `batch_id` precisa ser exatamente o ID recebido.
- Corpo JSON entre 1 e 512 bytes.
- Enviar cabeçalho **Content-Length** correto.
- Não usar streaming/chunked para essa resposta.
- Não usar 204, 202 ou redirecionamento para confirmar o lote.
- Não devolver HTML, página de login ou apenas `{"ok":true}`.

Frameworks costumam definir Content-Length ao retornar uma resposta JSON normal. Confira a resposta real com `curl -i`.

**O cliente somente libera seu lote após receber esse ACK válido.** Timeout, resposta perdida ou ACK inválido causam reenvio da mesma identidade e do mesmo conteúdo.

## 4. Persistência sem duplicação

No banco, crie uma restrição única para `(device_id, boot_id, batch_seq)` ou para `batch_id`, além da associação do dispositivo com seu token.

Fluxo de referência para sua implementação:

1. Validar e autenticar.
2. Conferir se a identidade já existe.
3. Se não existe: salvar conteúdo e metadados; confirmar somente após commit/persistência.
4. Se existe e o conteúdo é igual: retornar ACK 200.
5. Se existe e o conteúdo diverge: responder 409 e não sobrescrever.

Faça essa proteção também para duas requisições concorrentes: uma consulta anterior ao INSERT não substitui a restrição única.

Você pode começar armazenando um JSON por lote ou um campo JSONB. Para campanhas maiores, separe metadados no banco e séries em arquivos/objetos. Se separar os dois, trate o caso de falha entre escrita do arquivo e commit: estado intermediário, escrita atômica e reconciliação evitam ACK de lote irrecuperável.

Registre `received_at` em UTC no servidor. **Não substitua `t0_utc=null` pela hora de recebimento**: ela não é a hora de captura, especialmente após reenvios.

Sessão, equipamento e instalação ainda não são campos desse firmware. Na sua API, vincule o dispositivo a uma instalação/campanha antes de coletar e mantenha esse histórico. Se for necessário levar esses IDs no lote, combinaremos uma evolução do contrato antes da coleta rotulada.

## 5. Erros e reenvios

| Resposta | Comportamento do ESP32 |
| --- | --- |
| 200/201 com ACK correto | Libera lote e envia o próximo |
| 200/201 com ACK incorreto | Mantém lote e tenta novamente |
| Timeout, erro de conexão, 5xx, 408, 429 | Mantém lote; espera exponencial de aproximadamente 1 a 60 s, com pequena variação aleatória |
| Outros 4xx, incluindo 400, 401, 403, 404, 409, 413, 422 | Suspende a tentativa daquele lote; painel mostra rejeição; corrigir e reiniciar |
| 202, 204, 3xx | Não confirmam; reenvio, sem seguir redirecionamentos |

A API deve responder rapidamente. O cliente usa aproximadamente 3 s para conexão/leitura HTTP e 5 s para handshake TLS, sujeitos às condições de rede. Não faça treinamento, processamento longo ou consulta pesada antes do ACK. Nesta primeira versão, confirme persistência e processe depois.

Uma rejeição permanente exige intervenção. Reiniciar libera a tentativa, mas os lotes pendentes em RAM são perdidos. Essa é uma limitação explícita do MVP, não uma fila durável.

## 6. Como testar a sua API antes da placa

Na pasta do projeto, envie o exemplo sintético à sua API:

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/telemetry/batches \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer TOKEN_DE_TESTE_CADASTRADO_POR_VOCE' \
  -H 'Idempotency-Key: cv-0123456789ab.0123456789abcdef0123456789abcdef.0' \
  --data-binary @docs/exemplos/lote-sintetico.json
```

O token acima é apenas um marcador. Cadastre um token de teste associado ao ID sintético, ou ajuste os IDs do exemplo para seu ambiente.

Faça estes testes:

- [ ] Primeiro envio retorna 201 e ACK com ID correto.
- [ ] Mesmo envio retorna 200 e continua existindo uma única gravação.
- [ ] Mesmo ID com uma amostra alterada retorna 409.
- [ ] Token incorreto é recusado.
- [ ] Contagem ou formato incorreto é recusado.
- [ ] Reinicie seu backend e verifique se os lotes confirmados continuam acessíveis.
- [ ] Verifique Content-Length e o limite de 512 bytes no ACK.
- [ ] Ao configurar a placa, compare o contador de confirmados com o banco.

## 7. Conectar o ESP32 ao seu computador

O ESP32 e o computador precisam alcançar um ao outro pela rede.

Se você escolher FastAPI e implementar `main.py` com `app`, um exemplo de execução local é:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Isso disponibiliza o servidor nas interfaces da máquina. Use apenas na rede de desenvolvimento e ajuste o firewall à rede/porta necessárias.

Descubra o IPv4 local do computador com `hostname -I`. No painel da placa informe, por exemplo:

```text
http://192.168.1.50:8000/api/v1/telemetry/batches
```

Selecione **Usar HTTP na rede local (sem criptografia)**. O firmware aceita HTTP apenas em IPv4 literal privado (10/8, 172.16/12 ou 192.168/16), e não segue redirecionamentos.

Não use `localhost`, `127.0.0.1` nem `0.0.0.0` como destino no ESP32. Esses endereços não apontam para seu computador a partir da placa. Redes com isolamento de clientes podem impedir acesso mesmo com ambos no mesmo Wi-Fi.

### HTTPS

Para um servidor HTTPS, configure o endpoint e o certificado raiz CA em PEM no painel. O firmware verifica o certificado e aguarda sincronização de relógio por NTP. Não usa `setInsecure()`. O nome/IP no URL deve ser compatível com o certificado.

O token é individual e revogável. Não use senha do banco ou chave administrativa do provedor no dispositivo. HTTP de laboratório não protege o token contra observação na rede; utilize credencial descartável de teste.

## 8. Interpretação dos dados para o seu Python

A conversão de cada contagem `c` é:

```text
a_g = c / 16384
a_m_s2 = c × (9.80665 / 16384)
```

As séries incluem gravidade. O firmware não remove média, não aplica FFT e não treina modelo. O painel mostra conexão e diagnóstico de coleta; o processamento dos dados fica com você.

Para reconstruir o tempo nominal dentro de um lote:

```text
t_i = t0_monotonic_us + i × dt_us
```

Esse tempo é **estimado** a partir do FIFO e da taxa nominal; não é timestamp de interrupção para cada amostra. Não compare diretamente tempos monotônicos entre boots. Não trate a taxa como medida de precisão metrológica.

Não una trechos de `segment_id` diferentes como sinal contínuo. Também detecte lacunas entre `first_sample_index` e a amostra anterior: lotes podem ser descartados por fila cheia.

O início do segmento reinicia `first_sample_index`, mas a sequência de lotes continua crescente no boot. `clipped_samples>0` indica proximidade de saturação. Falta de lotes não indica que a máquina ficou normal.

## 9. Limites desta primeira entrega

- A coleta é somente do acelerômetro; não depende de calibrar um giroscópio em máquina parada.
- O MPU mantém circuitos internos de clock, mas a FIFO transmite apenas os seis bytes XYZ.
- A fila é RAM: seis lotes mais um em tentativa de envio, aproximadamente sete segundos de sinal. O lote em formação e o último lote para download são buffers adicionais.
- Sem API configurada, não há fila para envio; o painel preserva apenas o último lote completo para download.
- Se a fila lotar, novos lotes são descartados e contados; os antigos não são substituídos silenciosamente.
- Reinício/queda de energia perde o que não foi confirmado. Não há cartão SD nem armazenamento offline durável.
- Reset/erro/overflow do sensor descarta a janela parcial e inicia novo segmento; não inventa amostras para preencher a lacuna.
- Perfil inicial: ±2 g, 500 Hz, DLPF 94 Hz. Não é uma especificação de diagnóstico para qualquer motor.
- Não há backend, acesso ao banco ou implementação de ML nesta entrega: essas frentes seguem com você.

Referências: [Wi-Fi Arduino-ESP32](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/wifi.html) e [biblioteca Adafruit MPU6050](https://adafruit.github.io/Adafruit_MPU6050/html/class_adafruit___m_p_u6050.html). O contrato de lotes e ACK deste documento é específico do firmware CascaVibe 0.2.0.
