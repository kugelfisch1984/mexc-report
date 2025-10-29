// build.js
const fs = require('fs');
const crypto = require('crypto');

const pw = process.env.DASH_PW;
if (!pw) {
  console.error('DASH_PW not set');
  process.exit(1);
}

// starker zufälliger Salt pro Build
const salt = crypto.randomBytes(16).toString('hex');
const hash = crypto.createHash('sha256').update(`${salt}:${pw}`).digest('hex');


const outDir = 'dist';
if (!fs.existsSync(outDir)) fs.mkdirSync(outDir);

fs.writeFileSync(`${outDir}/config.js`, `window.__AUTH_CFG__={salt:"${salt}", hash:"${hash}"};`, 'utf8');

// statische Dateien kopieren
fs.cpSync('src', outDir, { recursive: true });
console.log('Built with salted hash config.');
