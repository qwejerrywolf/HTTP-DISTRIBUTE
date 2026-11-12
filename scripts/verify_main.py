"""Start the actual main.py with disposable credentials, then optionally run browser QA."""
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import httpx
from config import Config
from app.context import Context


def main():
    probes = [socket.socket(), socket.socket()]
    for probe in probes:
        probe.bind(('127.0.0.1', 0))
    download_port, admin_port = [probe.getsockname()[1] for probe in probes]
    for probe in probes:
        probe.close()
    public_base = f'http://127.0.0.1:{download_port}'
    admin_base = f'http://127.0.0.1:{admin_port}'
    with tempfile.TemporaryDirectory(prefix='httpd-verification-') as tmp:
        root=Path(tmp);files=root/'files';files.mkdir()
        (files/'示例资料.bin').write_bytes(bytes(range(256))*4096)
        for name in ('笔记.txt', '照片.JPG', '音乐.mp3', '影片.mkv'):
            (files/name).write_bytes(b'icon verification fixture')
        (files/'分享目录').mkdir();(files/'分享目录'/'说明.txt').write_text('单个文件下载与断点续传',encoding='utf-8')
        config=dict(download_host='127.0.0.1',download_port=download_port,admin_host='127.0.0.1',admin_port=admin_port,public_url=public_base,data_dir=str(root/'data'))
        cfg=root/'config.json';cfg.write_text(json.dumps(config),encoding='utf-8')
        ctx=Context(Config(**config));password=secrets.token_urlsafe(24)
        ctx.auth.initialize('browser-test',password)
        ctx.db.execute("INSERT INTO requests(started,ended,ip,filename,method,status,bytes,outcome,duration) VALUES (?,?,'192.0.2.25','机房提示测试.bin','GET',200,100,'complete',1)", (time.time()-1,time.time()))
        ctx.geoip._save('192.0.2.25','测试 IDC机房',86400)
        ctx.close()
        child_env={**os.environ,'HTTPD_CONFIG':str(cfg),'HTTPD_TEST_PASSWORD':password,'HTTPD_TEST_FILES':str(files),'PYTHONUTF8':'1'}
        child_env.update(HTTPD_TEST_ADMIN_URL=admin_base, HTTPD_TEST_PUBLIC_URL=public_base)
        child_env.pop('HTTPD_DATA',None)
        output=ROOT/'.work';output.mkdir(exist_ok=True)
        with open(output/'main-verification.log','w',encoding='utf-8') as logs:
            proc=subprocess.Popen([sys.executable,'main.py'],cwd=ROOT,env=child_env,stdout=logs,stderr=logs,
                                  creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0)
            try:
                deadline=time.time()+15
                with httpx.Client(timeout=2) as client:
                    while True:
                        if proc.poll() is not None:raise RuntimeError('main.py 提前退出，检查 .work/main-verification.log')
                        try:
                            if client.get(admin_base+'/').status_code==200 and proc.poll() is None:break
                        except httpx.TransportError:pass
                        if time.time()>deadline:raise TimeoutError('等待服务启动超时')
                        time.sleep(.1)
                    assert client.get(public_base+'/api/links').status_code==404
                    assert client.get(admin_base+'/api/links').status_code==401
                if os.environ.get('HTTPD_NODE'):
                    subprocess.run([os.environ['HTTPD_NODE'],'scripts/verify_browser.cjs'],cwd=ROOT,env=child_env,check=True)
                    subprocess.run([os.environ['HTTPD_NODE'],'scripts/verify_clipboard.cjs'],cwd=ROOT,env=child_env,check=True)
                print('实际 main.py 双端口启动及路由隔离：通过')
                duplicate=subprocess.run([sys.executable,'main.py'],cwd=ROOT,env=child_env,capture_output=True,timeout=10)
                assert duplicate.returncode!=0,'相同数据目录重复启动未被拒绝'
                assert '此数据目录已有运行中的服务' in duplicate.stderr.decode('utf-8')
                print('同数据目录单实例锁：通过')
                conflict_config={**config,'data_dir':str(root/'conflict-data')}
                conflict_file=root/'conflict.json';conflict_file.write_text(json.dumps(conflict_config),encoding='utf-8')
                conflict_ctx=Context(Config(**conflict_config));conflict_ctx.auth.initialize('conflict-test',password);conflict_ctx.close()
                conflict=subprocess.run([sys.executable,'main.py'],cwd=ROOT,env={**child_env,'HTTPD_CONFIG':str(conflict_file)},capture_output=True,timeout=10)
                assert conflict.returncode!=0 and '无法绑定服务端口' in conflict.stderr.decode('utf-8')
                print('端口冲突明确报错并退出：通过')
            finally:
                if proc.poll() is None:
                    if os.name=='nt':proc.send_signal(signal.CTRL_BREAK_EVENT)
                    else:proc.send_signal(signal.SIGTERM)
                    try:proc.wait(timeout=12)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
                assert proc.returncode==0,'主进程未优雅退出'
                print('主进程信号优雅关闭：通过')
        print('验证服务已停止；临时管理员、文件及数据库已清理')


if __name__=='__main__':main()
