'use strict';
// Build a clean client payload; never copy inventory, SSH settings, or backend code.
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
async function main() {
  const [rid, updaterArtifacts] = process.argv.slice(2);
  if (!['win-x64', 'linux-x64'].includes(rid) || !updaterArtifacts) throw Error('Usage: node scripts/build-release.cjs <win-x64|linux-x64> <native-updater-artifacts>');
  if ((rid.startsWith('win') ? 'win32' : 'linux') !== process.platform) throw Error('Build each release on its matching operating system.');
  const root = path.resolve(__dirname, '..');
  const pkg = require('../package.json');
  const output = path.join(root, 'release-staging', pkg.version, rid);
  if (fs.existsSync(output)) throw Error('Build directory already exists; archive it before rebuilding: '+output);
  fs.mkdirSync(output, {recursive:true});
  const {downloadArtifact} = await import('@electron/get');
  const zip = await downloadArtifact({version:pkg.devDependencies.electron,artifactName:'electron',platform:rid.startsWith('win')?'win32':'linux',arch:'x64'});
  const extracted = spawnSync(process.platform === 'win32' ? 'python' : 'python3', ['-c',
    'import os,sys,zipfile,pathlib; z=zipfile.ZipFile(sys.argv[1]); root=pathlib.Path(sys.argv[2]).resolve(); assert all(root in (root/i.filename).resolve().parents for i in z.infolist()); z.extractall(root); [(root/i.filename).chmod((i.external_attr >> 16) & 0o777) for i in z.infolist() if os.name != "nt" and (i.external_attr >> 16) & 0o777]',zip,output], {stdio:'inherit'});
  if (extracted.status !== 0) throw Error('Electron extraction failed');
  const windows = rid.startsWith('win');
  if (windows) fs.renameSync(path.join(output,'electron.exe'),path.join(output,'RebornITOperations.exe'));
  const application = path.join(output,'resources','app');
  fs.mkdirSync(application,{recursive:true});
  for (const name of ['desktop.cjs','preload.cjs','assets']) fs.cpSync(path.join(root,name),path.join(application,name),{recursive:true});
  fs.writeFileSync(path.join(application,'package.json'),JSON.stringify({name:pkg.name,productName:pkg.productName,version:pkg.version,main:pkg.main}));
  const suffix=windows?'.exe':'';
  for (const [folder,name] of [['bootstrap','RebornUpdateBootstrap'],['launcher','RebornAppLauncher']]) fs.copyFileSync(path.join(updaterArtifacts,folder,name+suffix),path.join(output,name+suffix));
  const config=JSON.parse(fs.readFileSync(path.join(root,'updates','app.json'),'utf8'));
  config.currentVersion=pkg.version;
  config.executable=windows?'RebornITOperations.exe':'reborn-it.sh';
  fs.writeFileSync(path.join(output,'rebornitoperations.app.json'),JSON.stringify(config,null,2));
  fs.copyFileSync(path.join(root,'updates','agent-bootstrap.json'),path.join(output,'agent-bootstrap.json'));
  fs.writeFileSync(path.join(output,'reborn-launch.json'),JSON.stringify({schema:1,appId:config.appId,displayName:config.displayName,fallbackExecutable:config.executable}));
  if (!windows) {
    fs.writeFileSync(path.join(output,'reborn-it.sh'),fs.readFileSync(path.join(root,'scripts','reborn-it.sh'),'utf8').replace(/\r\n/g,'\n'),{mode:0o755});
    for (const name of ['reborn-it.sh','RebornUpdateBootstrap','RebornAppLauncher']) fs.chmodSync(path.join(output,name),0o755);
  }
  if (windows) {
    const revision=spawnSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'});
    if(revision.status!==0) throw Error('Cannot record release source revision');
    fs.writeFileSync(path.join(output,'build-provenance.json'),JSON.stringify({sourceCommit:revision.stdout.trim(),version:pkg.version,rid,buildOS:'Windows',architecture:process.arch,node:process.version},null,2));
  }
  console.log(output);
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
