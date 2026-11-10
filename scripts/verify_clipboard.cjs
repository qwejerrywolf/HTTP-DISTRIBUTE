// Test actual copy/paste on an HTTP hostname, not localhost's secure-context exception.
const {chromium}=require(process.env.HTTPD_PLAYWRIGHT||'playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome',args:['--host-resolver-rules=MAP httpd-copy.test 127.0.0.1','--no-proxy-server']});
 const errors=[];
 const remote=url=>{const u=new URL(url);u.hostname='httpd-copy.test';return u.href;};
 async function pasted(page,expected){
  await page.evaluate(()=>{const box=document.createElement('textarea');box.id='paste-check';(document.querySelector('dialog[open]')||document.body).appendChild(box);box.focus();});
  await page.locator('#paste-check').press('Control+V');
  if(await page.locator('#paste-check').inputValue()!==expected)throw new Error('Actual clipboard paste differs from expected text');
  await page.locator('#paste-check').evaluate(e=>e.remove());
 }
 try{
  const page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(remote(process.env.HTTPD_TEST_ADMIN_URL));
  if(await page.evaluate(()=>isSecureContext||Boolean(navigator.clipboard)))throw new Error('Expected plain HTTP clipboard restrictions');
  await page.locator('[name=username]').fill('browser-test');await page.locator('[name=password]').fill(process.env.HTTPD_TEST_PASSWORD);await page.locator('#login-form button').click();
  await page.locator('#shell').waitFor({state:'visible'});
  await page.locator('#path').fill(process.env.HTTPD_TEST_FILES);await page.locator('#path').press('Enter');
  await page.locator('#file-list tr').filter({hasText:'示例资料.bin'}).click();await page.locator('#create-link').click();await page.locator('#share-go').click();
  const url=await page.locator('#created-url').inputValue();
  await page.locator('#copy-created').click();await page.waitForFunction(()=>document.querySelector('#toast').textContent==='已复制');
  await pasted(page,url);await page.locator('#modal-close').click();
  await page.locator('[data-view=links]').click();await page.locator('[data-link-action=copy]').first().click();
  await page.waitForFunction(()=>document.querySelector('#toast').textContent==='已复制');await pasted(page,url);
  const folder=await page.evaluate(async()=>(await (await fetch('/api/links?kind=folder')).json()).items[0].url);
  const buyer=await browser.newPage();buyer.on('pageerror',e=>errors.push(e.message));await buyer.goto(remote(folder));
  const fileURL=await buyer.locator('input[readonly]').first().inputValue();await buyer.locator('[data-copy]').first().click();
  await buyer.waitForFunction(()=>document.querySelector('[data-copy]').textContent==='已复制');await pasted(buyer,fileURL);
  // If compatibility copying is also blocked, never falsely report success.
  await buyer.evaluate(()=>{document.execCommand=()=>false;});await buyer.locator('[data-copy]').first().click();
  await buyer.waitForFunction(()=>document.querySelector('[data-copy]').textContent.includes('Ctrl+C'));
  const secure=await browser.newPage();await secure.goto(process.env.HTTPD_TEST_ADMIN_URL);
  await secure.evaluate(()=>{navigator.clipboard.writeText=async()=>{throw new DOMException('Denied','NotAllowedError');};const button=document.createElement('button');button.id='copy-denied-test';button.textContent='Copy';button.onclick=async()=>{window.copyResult=await copyToClipboard('clipboard permission fallback');};document.body.appendChild(button);});
  await secure.locator('#copy-denied-test').click();await secure.waitForFunction(()=>window.copyResult===true);await pasted(secure,'clipboard permission fallback');
  if(errors.length)throw new Error(errors.join('\n'));
  console.log('Clipboard: actual HTTP hostname copy/paste passed (creation dialog, link list, buyer page); denied-API fallback and manual fallback passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
