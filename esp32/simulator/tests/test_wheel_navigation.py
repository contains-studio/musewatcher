"""Link the wheel integration harness to built production simulator objects.

Usage: python3 tests/test_wheel_navigation.py --build build --out /tmp/wheel
Fixtures: reply text, checkerboard camera, solid cards, simulated wheel events.
No network calls or hardware photos are sent.
"""
import argparse, json, shlex, subprocess, tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--build',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
a=p.parse_args();build=a.build.resolve();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp)
    rows=json.loads((build/'compile_commands.json').read_text())
    row=next(r for r in rows if r['file'].endswith('/simulator/src/main.c'))
    args=shlex.split(row['command']);obj=tmp/'wheel.o';binary=tmp/'wheel'
    args[args.index('-o')+1]=str(obj)
    args[args.index('-c')+1]=str(Path(__file__).with_suffix('.c').resolve())
    subprocess.run(args,cwd=build,check=True)
    lines=(build/'build.ninja').read_text().splitlines()
    i=next(i for i,line in enumerate(lines) if line.startswith('build muse_simulator:'))
    objects=shlex.split(lines[i].split(':',1)[1])[1:];objects=objects[:objects.index('|')]
    objects=[str(obj) if o.endswith('/src/main.c.o') else o for o in objects]
    variables={}
    for line in lines[i+1:]:
        if line and not line.startswith(' '):
            break
        if '=' in line:
            key,value=line.strip().split('=',1)
            variables[key.strip()]=value.strip()
    # Keep CMake's link options, including ASan/UBSan when enabled in CI.
    flags=shlex.split(variables.get('LINK_FLAGS',''))
    libs=shlex.split(variables.get('LINK_LIBRARIES',''))
    subprocess.run([args[0],'-g',*flags,*objects,*libs,'-o',str(binary)],cwd=build,check=True)
    subprocess.run([str(binary),str(out)],check=True)
    # Compare actual rendered snapshots; don't rely on drawing-call mocks.
    assert (out/'05-saved-card.ppm').read_bytes()==(out/'08-recalled-card.ppm').read_bytes()
    assert (out/'14-cancelled-deferred-card.ppm').read_bytes() != (out/'15-recall-cancelled-card.ppm').read_bytes()
    assert (out/'19-native-weather.ppm').read_bytes() != (out/'20-native-weather-moving.ppm').read_bytes()
print('PASS recalled card is pixel-identical after camera use and failed download')
