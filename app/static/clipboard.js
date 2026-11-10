'use strict';

// HTTPS/localhost use the modern API; plain HTTP uses the selected-text copy command.
// Never read clipboard contents or place untrusted text into HTML.
window.copyToClipboard = async function(value) {
 const text=String(value);
 if(window.isSecureContext && navigator.clipboard?.writeText){
  try{await navigator.clipboard.writeText(text);return true;}catch{/* Try compatibility copy. */}
 }
 const active=document.activeElement;
 const selection=window.getSelection();
 const ranges=selection?[...Array(selection.rangeCount)].map((_,i)=>selection.getRangeAt(i).cloneRange()):[];
 const start=active?.selectionStart,end=active?.selectionEnd;
 const field=document.createElement('textarea');
 field.value=text;field.readOnly=true;field.tabIndex=-1;
 field.setAttribute('aria-label','待复制内容');
 // A control outside a modal dialog is inert, so use the open dialog when present.
 const parent=document.querySelector('dialog[open]')||document.body;
 field.style.position='fixed';field.style.opacity='0';field.style.width='1px';field.style.height='1px';field.style.top='0';field.style.left='0';
 parent.appendChild(field);
 try{
  field.focus({preventScroll:true});field.select();field.setSelectionRange(0,text.length);
  return document.execCommand('copy')===true;
 }catch{return false;}
 finally{
  field.remove();
  if(active?.isConnected){active.focus({preventScroll:true});if(typeof start==='number'&&active.setSelectionRange)active.setSelectionRange(start,end);}
  if(selection){selection.removeAllRanges();for(const range of ranges)selection.addRange(range);}
 }
};
