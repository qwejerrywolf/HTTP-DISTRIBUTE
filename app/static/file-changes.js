'use strict';

// Uses the authenticated API and existing file selection from app.js.
$('#check-files').onchange=e=>{state.selected=e.target.checked?new Set(state.rows.map((_,i)=>i)):new Set();selection();};
$('#delete-files').onclick=()=>run(deleteSelected);
async function deleteSelected(){
 const paths=[...state.selected].map(i=>state.rows[i].path);
 if(!paths.length)throw new Error('请先选择要删除的文件或文件夹');
 const preview=await post('/files/delete-preview',{paths});
 modal(`<h2>确认永久删除 ${preview.items.length} 个选中对象？</h2><p class="error">将直接删除磁盘上的文件和文件夹，不进入回收站。文件夹内的所有内容也会删除。</p><div class="delete-summary">包含 ${preview.files} 个文件、${preview.folders} 个文件夹 · ${fileSize(preview.bytes)}</div><ul class="delete-paths">${preview.items.map(r=>`<li>${r.icon_svg}<strong>${esc(r.name)}</strong><span>${esc(r.path)}</span></li>`).join('')}</ul><p>共用分享的对象需先删除关联授权。确认后如内容发生变化，任务会停止并提示重新确认。</p><div class="modal-actions"><button id="cancel-file-delete">取消</button><button id="confirm-file-delete" class="danger">确认永久删除</button></div>`);
 $('#cancel-file-delete').onclick=closeModal;
 $('#confirm-file-delete').onclick=()=>run(async()=>{
  $('#confirm-file-delete').disabled=true;
  try{await post('/files/delete',{confirmation:preview.confirmation});closeModal();await loadTasks();toast('删除任务已提交，可在文件操作任务中查看结果');}
  catch(e){if($('#confirm-file-delete'))$('#confirm-file-delete').disabled=false;throw e;}
 });
}

let uploadBatch=null;
function chooseUpload(){if(!state.path)throw new Error('请先打开上传目标目录');if(uploadBatch)throw new Error('已有上传任务，请等待完成或取消');$('#upload-input').value='';$('#upload-input').click();}
$('#upload-files').onclick=()=>run(chooseUpload);
$('#upload-input').onchange=()=>run(()=>{
 const files=[...$('#upload-input').files];if(!files.length)return;
 const directory=state.path;
 modal(`<h2>上传 ${files.length} 个文件</h2><div class="object-card"><strong>目标目录</strong><small>${esc(directory)}</small></div><ul class="delete-paths">${files.map(f=>`<li><strong>${esc(f.name)}</strong><span>${fileSize(f.size)}</span></li>`).join('')}</ul><label>同名冲突处理<select id="upload-conflict"><option value="cancel">跳过并报告失败，不覆盖</option><option value="rename">自动重命名</option><option value="overwrite">覆盖同名普通文件</option></select></label><p>文件逐个上传，完整接收后才写入目标名称。已关联分享的文件不能被覆盖。</p><div class="modal-actions"><button id="upload-start" class="primary">开始上传</button></div>`);
 $('#upload-start').onclick=()=>{const conflict=$('#upload-conflict').value;closeModal();run(()=>startUploads(files,directory,conflict));};
});
function renderUploads(){
 if(!uploadBatch)return;
 $('#upload-panel').hidden=false;
 $('#upload-list').innerHTML=uploadBatch.items.map(item=>`<div class="task-row"><div class="task-label"><strong>${esc(item.file.name)}</strong><small>${esc(item.message)} · ${fileSize(item.done)} / ${fileSize(item.file.size)}</small></div><progress max="${Math.max(item.file.size,1)}" value="${item.status==='completed'?Math.max(item.file.size,1):item.done}"></progress>${badge(item.status)}</div>`).join('');
}
function uploadOne(batch,item){return new Promise((resolve,reject)=>{
 const xhr=new XMLHttpRequest();batch.xhr=xhr;
 xhr.open('PUT','/api/files/upload?'+new URLSearchParams({directory:batch.directory,name:item.file.name,size:item.file.size,conflict:batch.conflict}));
 xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.setRequestHeader('X-HTTPD-Request','1');xhr.setRequestHeader('X-CSRF-Token',state.csrf);
 xhr.upload.onprogress=e=>{item.done=e.loaded;item.message=e.loaded===item.file.size?'正在提交文件…':'上传中';renderUploads();};
 xhr.onload=()=>{let body;try{body=JSON.parse(xhr.responseText);}catch{body={detail:'服务响应异常'};}
  if(xhr.status>=200&&xhr.status<300){item.done=item.file.size;resolve(body);}else{if(xhr.status===401)showLogin();reject(new Error(typeof body.detail==='string'?body.detail:'上传失败'));}};
 xhr.onerror=()=>reject(new Error('连接中断，文件未完整上传；请检查任务记录后重试'));
 xhr.onabort=()=>reject(new Error('已取消；若取消发生在提交阶段，请核对任务结果'));
 xhr.send(item.file);
});}
async function startUploads(files,directory,conflict){
 const batch={directory,conflict,cancelled:false,xhr:null,items:files.map(file=>({file,status:'queued',done:0,message:'等待上传'}))};
 uploadBatch=batch;$('#cancel-uploads').disabled=false;renderUploads();
 try{
  for(const item of batch.items){
   if(batch.cancelled){item.status='cancelled';item.message='未上传';renderUploads();continue;}
   item.status='running';renderUploads();
   try{const result=await uploadOne(batch,item);item.status='completed';item.message='已保存：'+result.file.path;}
   catch(e){item.status=batch.cancelled?'cancelled':'failed';item.message=e.message;}
   finally{batch.xhr=null;renderUploads();}
  }
 }finally{
  $('#cancel-uploads').disabled=true;uploadBatch=null;
  await loadTasks();if(state.path===directory)await browse(directory,false);
  toast('上传队列已结束，请查看各文件结果');
 }
}
$('#cancel-uploads').onclick=()=>{if(uploadBatch){uploadBatch.cancelled=true;uploadBatch.xhr?.abort();$('#cancel-uploads').disabled=true;}};
window.addEventListener('beforeunload',e=>{if(uploadBatch){e.preventDefault();e.returnValue='';}});
