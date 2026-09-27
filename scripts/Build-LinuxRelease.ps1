param([Parameter(Mandatory)][string]$HostName,[Parameter(Mandatory)][string]$Version)
$ErrorActionPreference='Stop'
if($HostName -notmatch '^[a-zA-Z0-9_.@-]+$' -or $Version -notmatch '^\d+\.\d+\.\d+$'){throw 'Invalid build host or version'}
$root=Split-Path $PSScriptRoot -Parent
$commit=(& git -C $root rev-parse HEAD).Trim()
if($LASTEXITCODE -ne 0 -or $commit -notmatch '^[a-f0-9]{40}$'){throw 'A committed source revision is required'}
if(& git -C $root status --porcelain --untracked-files=no){throw 'Commit the exact release source before building'}
$scratch=Join-Path $root '.build-temp'
$null=New-Item -ItemType Directory -Force $scratch
$source=Join-Path $scratch "source-$Version-$commit.tar"
$archive=Join-Path $scratch "linux-$Version-$commit.tar.gz"
$remote=".cache/reborn-it-releases/$Version-$commit"
$output=Join-Path $root "release-staging\$Version\linux-x64"
if(Test-Path -LiteralPath $output){throw "Build output exists; archive it first: $output"}
& git -C $root archive --format=tar --output=$source HEAD
if($LASTEXITCODE -ne 0){throw 'Source archive failed'}
& ssh $HostName "mkdir -p ~/$remote"
if($LASTEXITCODE -ne 0){throw 'Build host unavailable'}
& scp $source "${HostName}:$remote/source.tar"
if($LASTEXITCODE -ne 0){throw 'Source transfer failed'}
$build=@'
set -eu
cd ~/{REMOTE}
mkdir -p source native/bootstrap native/launcher
tar -xf source.tar -C source
python3 - <<'PY'
import json,pathlib,shutil
home=pathlib.Path.home()/'.local/share/Reborn Entertainment/UpdateAgent'
active=json.loads((home/'agent-active.json').read_text())
for folder,name in [('bootstrap','RebornUpdateBootstrap'),('launcher','RebornAppLauncher')]:
    shutil.copy2(pathlib.Path(active['directory'])/name,pathlib.Path('native')/folder/name)
PY
cd source
npm ci --ignore-scripts --no-audit --no-fund
node scripts/build-release.cjs linux-x64 ../native
node --check desktop.cjs
python3 - <<'PY'
import json,pathlib,platform,subprocess
root=pathlib.Path('release-staging/{VERSION}/linux-x64')
for name in ['electron','chrome_crashpad_handler','RebornUpdateBootstrap','RebornAppLauncher','reborn-it.sh']:
    assert (root/name).stat().st_mode & 0o111,name
record={'sourceCommit':'{COMMIT}','version':'{VERSION}','rid':'linux-x64','buildOS':platform.system(),'architecture':platform.machine(),'node':subprocess.check_output(['node','--version'],text=True).strip()}
(root/'build-provenance.json').write_text(json.dumps(record,indent=2))
print(json.dumps(record))
PY
tar -czf ../linux-payload.tar.gz -C release-staging/{VERSION}/linux-x64 .
'@
$build=$build.Replace('{REMOTE}',$remote).Replace('{VERSION}',$Version).Replace('{COMMIT}',$commit)
$build | & ssh $HostName 'bash -s'
if($LASTEXITCODE -ne 0){throw 'Native Linux build failed'}
& scp "${HostName}:$remote/linux-payload.tar.gz" $archive
if($LASTEXITCODE -ne 0){throw 'Linux payload transfer failed'}
$null=New-Item -ItemType Directory -Force $output
& tar -xzf $archive -C $output
if($LASTEXITCODE -ne 0){throw 'Linux payload extraction failed'}
Write-Output "Linux payload built on $HostName at commit $commit, imported to $output"
