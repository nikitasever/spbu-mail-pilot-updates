import hashlib,json,os,pathlib,re,subprocess
metadata=pathlib.Path('dist/update.json')
if not metadata.exists():print('No APK submitted yet.');raise SystemExit(0)
data=json.loads(metadata.read_text());name=data['versionName'];code=data['versionCode']
if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}',name) or type(code)!=int or code<1 or data['packageId']!='ru.local.spbumailpilot':raise ValueError('Invalid release metadata')
repo='nikitasever/spbu-mail-pilot-updates';tag='v'+name;filename='spbu-mail-pilot-'+name+'.apk';apk=pathlib.Path('dist')/filename
if data['url']!='https://github.com/'+repo+'/releases/download/'+tag+'/'+filename or hashlib.sha256(apk.read_bytes()).hexdigest()!=data['sha256']:raise ValueError('Invalid APK checksum or URL')
sdk=pathlib.Path(os.environ['ANDROID_HOME']);tools=sorted((sdk/'build-tools').iterdir())[-1]
verified=subprocess.check_output([str(tools/'apksigner'),'verify','--print-certs',str(apk)],text=True)
if '197ecdabd94c7217afa45df6f02300f1f0ff12474b3146fc4018a3adbb8ce7b6' not in verified:raise ValueError('Wrong signing certificate')
badging=subprocess.check_output([str(tools/'aapt'),'dump','badging',str(apk)],text=True)
if "package: name='ru.local.spbumailpilot' versionCode='"+str(code)+"' versionName='"+name+"'" not in badging:raise ValueError('Manifest mismatch')
exists=subprocess.run(['gh','release','view',tag,'--repo',repo],capture_output=True)
if exists.returncode==0:print('Release is already published; not replacing it.');raise SystemExit(0)
subprocess.run(['gh','release','create',tag,str(apk),str(metadata),'--repo',repo,'--target',os.environ['GITHUB_SHA'],'--title','Почта · тест '+name,'--notes-file','dist/release-notes.txt','--latest'],check=True)
