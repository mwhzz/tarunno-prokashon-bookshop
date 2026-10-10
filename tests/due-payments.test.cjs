const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/due-payments.js'),'utf8');
function harness(storage=new Map()){
  const nodes=new Map(),messages=[];
  const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,disabled:false,textContent:'',innerHTML:'',style:{},removed:false,focus(){},remove(){this.removed=true;}});return nodes.get(id);};
  const context=vm.createContext({Intl,Math,JSON,Number,Object,String,Error,AbortController,crypto:crypto.webcrypto,
    API:'/api',getCookie:()=>'',setTimeout:()=>1,clearTimeout(){},num:v=>String(v),icon:()=>'',toast:t=>messages.push(t),wireModalDismiss(){},
    api:async()=>({invoices:[],customer_dues:[]}),window:{addEventListener(){}},
    document:{getElementById:node,body:{insertAdjacentHTML(){node("payment-modal").removed=false;}},addEventListener(){}},
    localStorage:{setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k),removeItem:k=>storage.delete(k)}});
  vm.runInContext(source,context);
  const run=code=>vm.runInContext(code,context);
  run("openDuePayment('sale',7,1000)");node('pay-amount').value='100.00';node('pay-discount').value='20.00';node('pay-method').value='cash';
  return {run,node,context,storage,messages};
}
test('double clicks send one POST and keep the modal while payment is pending',async()=>{
  const h=harness();let calls=0,release;
  h.context.postDuePayment=async()=>{calls++;await new Promise(r=>release=r);return{id:7};};
  const first=h.run('submitDuePayment()');await h.run('submitDuePayment()');
  assert.equal(calls,1);assert.ok(h.storage.get('due_payment_pending_v1'));
  h.run('dismissDuePayment()');assert.equal(h.node('payment-modal').removed,false);
  release();await first;assert.equal(h.node('payment-modal').removed,true);assert.equal(h.storage.size,0);
});
test('uncertain partial payment retries the exact frozen payload and request ID',async()=>{
  const h=harness();let calls=0,payload;
  h.context.postDuePayment=async attempt=>{calls++;if(calls===1){payload=JSON.stringify(attempt.payload);throw {uncertain:true,message:'offline'};}assert.equal(JSON.stringify(attempt.payload),payload);return{id:7};};
  await h.run('submitDuePayment()');assert.equal(h.node('pay-amount').disabled,true);
  h.node('pay-amount').value='900';await h.run('submitDuePayment()');
  assert.equal(calls,2);assert.equal(h.run('dueAttempt'),null);
});
test('refresh restores a pending payment without automatically sending it again',async()=>{
  const h=harness();let payload;
  h.context.postDuePayment=async attempt=>{payload=JSON.stringify(attempt.payload);throw {uncertain:true};};
  await h.run('submitDuePayment()');
  const restored=harness(h.storage);let calls=0;
  restored.context.postDuePayment=async attempt=>{calls++;assert.equal(JSON.stringify(attempt.payload),payload);return{id:7};};
  restored.run('resumePendingDue()');assert.equal(calls,0);assert.equal(restored.node('pay-amount').disabled,true);
  await restored.run('submitDuePayment()');assert.equal(calls,1);
});
test('known validation failure permits a corrected attempt with a new ID',async()=>{
  const h=harness();let first;
  h.context.postDuePayment=async attempt=>{first=attempt.payload.request_id;throw {uncertain:false,message:'Due changed'};};
  await h.run('submitDuePayment()');assert.equal(h.node('pay-amount').disabled,false);assert.equal(h.storage.size,0);
  h.context.postDuePayment=async attempt=>{assert.notEqual(attempt.payload.request_id,first);return{id:7};};
  h.node('pay-amount').value='50';await h.run('submitDuePayment()');
});
test('invalid amounts do not POST; Bengali decimals are accepted exactly',async()=>{
  const h=harness();let calls=0;
  h.context.postDuePayment=async attempt=>{calls++;assert.equal(attempt.payload.amount,'100.25');return{id:7};};
  for(const value of ['-1','NaN','Infinity','1.001','1001','1e2']){h.node('pay-amount').value=value;await h.run('submitDuePayment()');}
  assert.equal(calls,0);h.node('pay-amount').value='১০০.২৫';await h.run('submitDuePayment()');assert.equal(calls,1);
});
test('network errors and ambiguous server responses are marked uncertain',async()=>{
  const h=harness();h.context.fetch=async()=>{throw Error('offline');};
  await assert.rejects(h.run("postDuePayment({kind:'sale',id:7,payload:{}})"),e=>e.uncertain===true);
  h.context.fetch=async()=>({ok:true,status:200,json:async()=>null});
  await assert.rejects(h.run("postDuePayment({kind:'customer',id:7,payload:{}})"),e=>e.uncertain===true);
});
