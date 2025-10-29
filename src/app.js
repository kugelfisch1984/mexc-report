/* Minimaler Client-Auth + Demo-Datenabruf (du ersetzt die Demo-Funktionen später mit echter MEXC-API) */
async function sha256(txt){const buf=new TextEncoder().encode(txt);const h=await crypto.subtle.digest('SHA-256',buf);return [...new Uint8Array(h)].map(x=>x.toString(16).padStart(2,'0')).join('');}
function fmt(n,ccy){return new Intl.NumberFormat('de-DE',{style:'currency',currency: ccy==='EUR'?'EUR':'USD', maximumFractionDigits:2}).format(n);}
const $=s=>document.querySelector(s);

async function login(){
  const u=$("#user").value.trim();
  const p=$("#pass").value;
  const {salt,hash}=window.__AUTH_CFG__;
  const test=await sha256(`${salt}:${u}:${p}`); // Client vergleicht user:pass
  const ok = (await sha256(`${salt}:${u}:${p}`))===hash || (await sha256(`${salt}:${u}:${p}`))===hash; // doppelt ist egal – nur ein Aufruf
  if(!ok){ $("#msg").hidden=false; return; }
  $("#gate").classList.add('hide'); $("#app").classList.remove('hide');
  initApp();
}

$("#go").addEventListener('click', login);
$("#pass").addEventListener('keydown', e=>{ if(e.key==='Enter') login(); });

/* Demo: ersetze diese drei Funktionen später durch echte Requests an deine Backend-API/Cloud-Function
   Für reines GitHub Pages (statisch) kannst du JSON-Dateien committen, die ein Action-Job täglich erzeugt. */
async function fetchEquity(days){ // returns [{date:'YYYY-MM-DD', equity: number_usdt}, ...]
  // placeholder – ersetze durch Fetch auf /data/equity.json
  const out=[]; let base=1000; for(let i=days-1;i>=0;i--){ base*= (1+(Math.random()-0.5)*0.01); out.push({date: new Date(Date.now()-i*864e5).toISOString().slice(0,10), equity: base}); }
  return out;
}
async function fetchPnL(days){ // returns [{date:'YYYY-MM-DD', pnl:number_usdt}, ...]
  const out=[]; for(let i=days-1;i>=0;i--){ out.push({date: new Date(Date.now()-i*864e5).toISOString().slice(0,10), pnl: (Math.random()-0.5)*20}); }
  return out;
}
async function fetchCopyTrades(days){ // returns [{date,symbol,side,qty,price,fee,pnl,roi}]
  const syms=['BTC/USDT','ETH/USDT','SOL/USDT'];
  const out=[]; for(let i=0;i<30;i++){ const s=syms[i%syms.length]; const pnl=(Math.random()-0.5)*15; out.push({date:new Date(Date.now()-Math.random()*days*864e5).toISOString().slice(0,10), symbol:s, side: (Math.random()>0.5?'buy':'sell'), qty: +(Math.random()*0.2).toFixed(4), price: +(100+Math.random()*2000).toFixed(2), fee: +(Math.random()*0.2).toFixed(3), pnl: +pnl.toFixed(2), roi: +(pnl/100*100).toFixed(2)}); }
  return out.sort((a,b)=>a.date.localeCompare(b.date));
}

async function initApp(){
  const ccySel=$("#ccy"), rangeSel=$("#range");
  const render=async ()=>{
    const ccy=ccySel.value; const days=+rangeSel.value;
    const eq=await fetchEquity(days);
    const pn=await fetchPnL(days);
    const ct=await fetchCopyTrades(days);

    // einfache EUR/USDT Umrechnung (Placeholder: 1 USDT ≈ 0.93 EUR). Ersetze mit echtem Kurs.
    const fx = (ccy==='EUR') ? 0.93 : 1.0;

    const sumPn = pn.reduce((a,b)=>a+b.pnl,0);
    const eqNow = eq.at(-1)?.equity ?? 0;
    const eqStart = eq[0]?.equity ?? eqNow;
    const roi = eqStart ? ((eqNow-eqStart)/eqStart*100) : 0;

    $("#k_equity").textContent = fmt(eqNow*fx, ccy==='EUR'?'EUR':'USD');
    $("#k_pnl").textContent = fmt(sumPn*fx, ccy==='EUR'?'EUR':'USD');
    $("#k_roi").textContent = `${roi.toFixed(2)}%`;
    $("#k_copies").textContent = `${ct.length}`;

    drawLine('equityChart', eq.map(x=>x.date), eq.map(x=>x.equity*fx), 'Equity');
    drawBar('pnlChart', pn.map(x=>x.date), pn.map(x=>x.pnl*fx), 'Täglicher PnL');

    const tb=$("#tbl tbody"); tb.innerHTML='';
    ct.forEach(r=>{
      const tr=document.createElement('tr');
      tr.innerHTML=`<td>${r.date}</td><td>${r.symbol}</td><td>${r.side}</td>
        <td>${r.qty}</td><td>${r.price}</td><td>${r.fee}</td>
        <td>${fmt(r.pnl*fx, ccy==='EUR'?'EUR':'USD')}</td><td>${r.roi}%</td>`;
      tb.appendChild(tr);
    });
  };

  ccySel.onchange=render; rangeSel.onchange=render;
  await render();
}

let charts={};
function drawLine(id, labels, data, title){
  charts[id]?.destroy();
  charts[id]=new Chart(document.getElementById(id), {
    type:'line',
    data:{ labels, datasets:[{label:title, data}] },
    options:{ responsive:true, plugins:{ legend:{display:true}}, scales:{ x:{ticks:{maxRotation:0}}} }
  });
}
function drawBar(id, labels, data, title){
  charts[id]?.destroy();
  charts[id]=new Chart(document.getElementById(id), {
    type:'bar',
    data:{ labels, datasets:[{label:title, data}] },
    options:{ responsive:true, plugins:{ legend:{display:true}}, scales:{ x:{ticks:{maxRotation:0}}} }
  });
}
