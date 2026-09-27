'use strict';
// Build a clean client payload; never copy inventory, SSH settings, or backend code.
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
async function main() {
  const [rid, updaterArtifacts] = process.argv.slice(2);
  if (!['win-x64', 'linux-x64'].includes(rid) || !updaterArtifacts) throw Error('Usage: node scripts/build-release.cjs <win-x64|linux-x64> <native-updater-artifacts>');
  const root = path.resolve(__dirname, '..');
  const pkg = require('../package.json');
  const output = path.join(root, 'release-staging', pkg.version, rid);
  if (fs.existsSync(output)) throw Error('Build directory already exists; archive it before rebuilding: '+output);
  fs.mkdirSync(output, {recursive:true});
  const {downloadArtifact} = await import('@electron/get');
  const zip = await downloadArtifact({version:pkg.devDependencies.electron,artifactName:'electron',platform:rid.startsWith('win')?'win32':'linux',arch:'x64'});
  const extracted = spawnSync('python', ['-m','zipfile','-e',zip,output], {stdio:'inherit'});
  if (extracted.status !== 0) throw Error('Electron extraction failed');
  const windows = rid.startsWith('win');
  if (windows) fs.renameSync(path.join(output,'electron.exe'),path.join(output,'RebornITOperations.exe'));
  const application = path.join(output,'resources','app');
  fs.mkdirSync(application,{recursive:true});
  for (const name of ['desktop.cjs','assets']) fs.cpSync(path.join(root,name),path.join(application,name),{recursive:true});
  fs.writeFileSync(path.join(application,'package.json'),JSON.stringify({name:pkg.name,productName:pkg.productName,version:pkg.version,main:pkg.main}));
  const suffix=windows?'.exe':'';
  for (const [folder,name] of [['bootstrap','RebornUpdateBootstrap'],['launcher','RebornAppLauncher']]) fs.copyFileSync(path.join(updaterArtifacts,folder,name+suffix),path.join(output,name+suffix));
  const config=JSON.parse(fs.readFileSync(path.join(root,'updates','app.json'),'utf8'));
  config.currentVersion=pkg.version;
  config.executable=windows?'RebornITOperations.exe':'reborn-it.sh';
  fs.writeFileSync(path.join(output,'rebornitoperations.app.json'),JSON.stringify(config,null,2));
  fs.copyFileSync(path.join(root,'updates','agent-bootstrap.json'),path.join(output,'agent-bootstrap.json'));
  fs.writeFileSync(path.join(output,'reborn-launch.json'),JSON.stringify({schema:1,appId:config.appId,displayName:config.displayName,fallbackExecutable:config.executable}));
  if (!windows) fs.copyFileSync(path.join(root,'scripts','reborn-it.sh'),path.join(output,'reborn-it.sh'));
  console.log(output);
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
