// A local unpacked Windows distribution. Data stays outside the runtime.
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname,'..');
const output = path.join(root,'desktop');
if (fs.existsSync(output)) throw new Error('desktop/ already exists. Close the app and archive that directory before rebuilding.');
const runtime = path.dirname(require('electron'));
fs.cpSync(runtime,output,{recursive:true});
fs.copyFileSync(path.join(output,'electron.exe'),path.join(output,'RebornITOperations.exe'));
const app = path.join(output,'resources','app');
fs.mkdirSync(app,{recursive:true});
for(const name of ['desktop.cjs','preload.cjs','assets']) fs.cpSync(path.join(root,name),path.join(app,name),{recursive:true});
const pkg = require('../package.json');
fs.writeFileSync(path.join(app,'package.json'),JSON.stringify({name:pkg.name,productName:pkg.productName,version:pkg.version,main:pkg.main},null,2));
console.log('Created desktop/RebornITOperations.exe. Inventory remains in data/.');
