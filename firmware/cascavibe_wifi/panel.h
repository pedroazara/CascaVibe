#pragma once
#include <pgmspace.h>
const char PANEL_HTML[] PROGMEM = R"CVHTML(
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CascaVibe</title>
<style>
:root{color-scheme:light;font-family:system-ui,sans-serif;color:#24292e;background:#f7f7f7}
*{box-sizing:border-box}body{margin:0}main{max-width:520px;margin:35px auto;padding:0 20px 35px}
h1{font-size:22px;margin:0 0 20px}p{font-size:14px;margin:8px 0}.muted{color:#626870}
label{display:block;font-size:14px;margin:16px 0 6px}
input,select,textarea,button{font:inherit}input:not([type=checkbox]),select,textarea{width:100%;background:#fff;color:#24292e;border:1px solid #b9bec4;border-radius:5px;padding:10px;font-size:15px}
textarea{min-height:100px;resize:vertical;font:12px monospace}
input:focus,select:focus,textarea:focus{outline:2px solid #3067bf;outline-offset:1px}
button{border:1px solid #b9bec4;border-radius:5px;padding:9px 13px;background:#fff;color:#24292e;cursor:pointer;font-size:14px}
button.primary{background:#245aa5;color:white;border-color:#245aa5}
button:disabled{opacity:.55;cursor:wait}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.check{font-size:13px;display:flex;align-items:center;gap:7px}.check input{margin:0}
details{margin:22px 0;border-top:1px solid #ddd;padding-top:15px}summary{cursor:pointer;font-size:14px;font-weight:600}
#networks{margin-top:10px}#message{white-space:pre-line;margin:14px 0;color:#8b351b;font-size:14px}
small{display:block;color:#626870;margin-top:5px;font-size:12px}.actions{margin-top:22px}
dl{display:grid;grid-template-columns:1fr auto;gap:9px;font-size:13px}dt{color:#626870}dd{margin:0;text-align:right;overflow-wrap:anywhere}
a{color:#245aa5;font-size:14px}footer{margin-top:26px;font-size:12px;color:#777}
</style>
</head>
<body><main>
<h1>CascaVibe</h1>
<p id="wifi" role="status">Conectando ao ESP32…</p>
<p class="muted" id="sensor">Sensor: aguardando</p>
<form id="settings">
<label for="ssid">Rede Wi-Fi</label>
<input id="ssid" maxlength="32" autocomplete="off" required>
<div style="margin-top:8px"><button type="button" id="scan">Buscar redes</button></div>
<div id="networks"></div>
<label for="password">Senha</label>
<input id="password" type="password" maxlength="63" autocomplete="new-password">
<small>Deixe vazio para manter a senha da mesma rede.</small>
<label class="check"><input type="checkbox" id="open_network">Rede sem senha</label>
<details id="api_settings">
<summary>API (opcional)</summary>
<label for="api_url">URL de envio</label>
<input id="api_url" type="url" maxlength="256" placeholder="https://servidor/api/v1/telemetry/batches">
<label for="api_token">Token</label>
<input id="api_token" type="password" maxlength="256" autocomplete="new-password">
<small id="token_hint">Deixe vazio para manter o token salvo.</small>
<label class="check"><input type="checkbox" id="allow_http">Usar HTTP na rede local (sem criptografia)</label>
<small>Para testes locais, use o IP do computador, não localhost.</small>
<details><summary>Certificado HTTPS e credenciais</summary>
<label for="root_ca">CA em formato PEM</label>
<textarea id="root_ca" maxlength="6000"></textarea>
<small id="ca_hint">Necessário para HTTPS. Vazio mantém a CA salva.</small>
<label class="check"><input type="checkbox" id="clear_token">Apagar token salvo</label>
<label class="check"><input type="checkbox" id="clear_ca">Apagar CA salva</label>
</details>
</details>
<div class="row actions"><button type="submit" class="primary" id="save">Salvar e conectar</button></div>
<div id="message" role="status" aria-live="polite"></div>
</form>
<details>
<summary>Diagnóstico</summary>
<p id="delivery">API não configurada.</p>
<dl><dt>IP na rede</dt><dd id="ip">—</dd><dt>Sinal Wi-Fi</dt><dd id="rssi">—</dd><dt>Taxa observada</dt><dd id="hz">—</dd><dt>Lotes confirmados</dt><dd id="ack">—</dd><dt>Pendentes / descartados</dt><dd id="queue">—</dd><dt>Overflows / erros I²C</dt><dd id="errors">—</dd><dt>HTTP</dt><dd id="http">—</dd></dl>
<div class="row"><a href="/api/batch">Baixar último lote</a><button type="button" id="reboot">Reiniciar</button></div>
</details>
<footer id="device">ESP32 · MPU-6050</footer>
<script>
'use strict';
const el=id=>document.getElementById(id);
const write=(id,value)=>{el(id).textContent=value;};
let csrf='',initialized=false,stopped=false;
async function request(path,options={}){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
  try{
    const response=await fetch(path,{cache:'no-store',...options,signal:controller.signal});
    const data=await response.json().catch(()=>({error:'Resposta inválida. Recarregue a página e confira o login.'}));
    if(!response.ok)throw Error(data.error||'HTTP '+response.status);
    return data;
  }finally{clearTimeout(timer);}
}
async function poll(){
  if(stopped)return;
  try{
    const s=await request('/api/status');csrf=s.csrf;
    write('wifi',s.wifi_connected?'Wi-Fi conectado: '+s.wifi_ssid:'Wi-Fi desconectado');
    write('sensor',s.sensor_ok?'Sensor funcionando':'Sensor sem dados. Confira as ligações.');
    write('ip',s.wifi_connected?s.ip:'—');write('rssi',s.wifi_connected?s.rssi+' dBm':'—');
    write('hz',s.sensor_ok?Number(s.measured_hz).toFixed(0)+' Hz':'—');
    write('ack',s.acknowledged);write('queue',s.queue+' / '+s.dropped_batches);
    write('errors',s.overflows+' / '+s.read_errors);write('http',s.http_code||'Ainda não enviado');
    write('delivery',s.delivery);write('device',s.device_id+' · v'+s.firmware);
    if(!initialized){
      el('ssid').value=s.wifi_ssid;el('api_url').value=s.api_url;el('allow_http').checked=s.allow_http;
      el('open_network').checked=Boolean(s.open_network);
      write('token_hint',s.has_token?'Token salvo. Vazio mantém o atual.':'Nenhum token salvo.');
      write('ca_hint',s.has_ca?'CA salva. Vazio mantém a atual.':'HTTPS exige certificado raiz CA em PEM.');
      initialized=true;
    }
  }catch(error){
    write('wifi','Sem conexão com o ESP32. Confira a rede.');
    write('sensor','Sem dados atuais');write('hz','—');
    write('delivery','Estado indisponível: recarregue após reconectar.');
  }
  if(!stopped)setTimeout(poll,1000);
}
el('scan').onclick=async()=>{
  el('scan').disabled=true;write('networks','Buscando…');
  try{
    let data;
    for(let i=0;i<20;i++){
      data=await request('/api/networks');
      if(!data.scanning)break;
      await new Promise(resolve=>setTimeout(resolve,800));
    }
    if(data.scanning)throw Error('Busca demorou. Tente novamente ou digite o nome da rede.');
    el('networks').replaceChildren();
    const select=document.createElement('select');select.setAttribute('aria-label','Redes encontradas');
    const first=document.createElement('option');first.textContent='Selecione uma rede';first.value='';select.appendChild(first);
    const networks=(data.networks||[]).filter(n=>n.ssid);
    networks.forEach((n,i)=>{
      const option=document.createElement('option');option.value=String(i);
      option.textContent=n.ssid+' ('+n.rssi+' dBm)';select.appendChild(option);
    });
    select.onchange=()=>{
      if(select.value==='')return;
      const n=networks[Number(select.value)];el('ssid').value=n.ssid;el('open_network').checked=n.open;
    };
    if(networks.length)el('networks').appendChild(select);
    else write('networks','Nenhuma rede encontrada. Digite o nome manualmente.');
  }catch(error){write('networks',error.message);}
  finally{el('scan').disabled=false;}
};
el('settings').onsubmit=async(event)=>{
  event.preventDefault();
  if(!csrf){write('message','Aguarde a conexão com o ESP32.');return;}
  if(!confirm('Salvar reinicia o ESP32 e perde os lotes pendentes em memória. Continuar?'))return;
  el('save').disabled=true;
  const data={};
  ['ssid','password','api_url','api_token','root_ca'].forEach(id=>data[id]=el(id).value);
  ['open_network','allow_http','clear_token','clear_ca'].forEach(id=>data[id]=el(id).checked);
  try{
    await request('/api/config',{method:'POST',headers:{'Content-Type':'application/json','X-CV-CSRF':csrf},body:JSON.stringify(data)});
    stopped=true;
    write('message','Salvo. Reconecte à rede CascaVibe após o reinício e recarregue http://192.168.4.1 para conferir a conexão.');
  }catch(error){write('message',error.message);el('save').disabled=false;}
};
el('reboot').onclick=async()=>{
  if(!csrf||!confirm('Reiniciar? Os lotes pendentes em memória serão perdidos.'))return;
  try{
    await request('/api/reboot',{method:'POST',headers:{'X-CV-CSRF':csrf}});
    stopped=true;write('message','Reiniciando. Reconecte e recarregue a página.');
  }catch(error){write('message',error.message);}
};
poll();
</script></main></body></html>
)CVHTML";
