const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/pos-workspace.js'), 'utf8');

function harness() {
  const nodes = new Map(), storage = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {id, value:'', textContent:'', innerHTML:'', hidden:false,
      style:{}, classList:{add(){},remove(){},toggle(){}},focus(){},scrollIntoView(){},checkValidity(){return true;}});
    return nodes.get(id);
  };
  const context = vm.createContext({Intl, Number, Math, Object, Array, String, JSON, Date, Set,
    encodeURIComponent, setTimeout:()=>0,clearTimeout(){},
    window:{addEventListener(){}},localStorage:{setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k),removeItem:k=>storage.delete(k)},
    document:{getElementById:node,addEventListener(){},querySelector:node,querySelectorAll:()=>[...nodes.values()]},
    icon:()=>'',toast(){},confirmModal:async()=>true,api:async()=>({results:[],next:null})});
  vm.runInContext(source,context);
  for(const id of ['commission','discount','pack','courier','txfee','paid'])node(id).value='0';
  node('sale-type').value='retail';node('pay-method').value='cash';node('filter-sort').value='name';
  const run = code => vm.runInContext(code,context);
  run('catalogLoading=false;draftReady=true;');
  const book = {id:1,title:'Test <Book>',author:'Author',isbn:'9781234567890',is_active:true,mrp:'200.00',selling_price:'160.00',commission:'20.00',shop_quantity:3};
  context.sample=book;run('allBooks=[sample];');
  return {node,context,run,storage};
}

test('ISBN search, stock limits, retail and wholesale discounts, fractional totals',()=>{
  const h=harness();h.node('book-search').value='9781234567890';
  assert.equal(h.run('getFilteredBooks().length'),1);
  h.run('addToCart(1);changeQty(1,1);changeQty(1,1);changeQty(1,1)');
  assert.equal(h.run('cart[0].qty'),3);
  h.node('commission').value='5';h.node('txfee').value='0.50';
  assert.equal(h.run('currentBillTotal()'),450.5); // 600 less 25% plus fee.
  h.node('sale-type').value='wholesale';assert.equal(h.run('currentBillTotal()'),570.5);
  h.run('renderCart()');assert.ok(h.node('cart-scroll').innerHTML.includes('Test &lt;Book&gt;'));
});

test('restores full draft after catalog load without overwriting it during initial render',async()=>{
  const h=harness();h.storage.set('pos_cart_draft_v1',JSON.stringify({cart:[{bid:1,qty:2,extraCommission:5}],fields:{'sale-type':'retail',commission:'7',discount:'28',pack:'5',courier:'0',txfee:'0','cus-name':'Buyer','cus-phone':'01700000000','pay-method':'cash',paid:'100'}}));
  h.context.api=async path=>path.includes('/books/')?{results:[h.context.sample],next:null}:{results:[],next:null};
  await h.run('initPOS()');
  assert.equal(h.run('cart.length'),1);assert.equal(h.run('cart[0].qty'),2);
  assert.equal(h.node('commission').value,'7');assert.equal(h.node('cus-name').value,'Buyer');
  assert.equal(h.node('paid').value,'100');assert.equal(h.node('pack').value,'5');
});

test('blocks unnamed due sales and negative charges before creating an invoice',async()=>{
  const h=harness();h.run('addToCart(1)');let posts=0;h.context.api=async()=>{posts++;};
  await h.run('completeSale()');assert.equal(posts,0);assert.ok(h.node('checkout-error').textContent);
  h.node('paid').value='200';h.node('pack').value='-1';await h.run('completeSale()');assert.equal(posts,0);
});

test('one POST for repeated clicks, records only bill amount, refresh failure keeps sale complete',async()=>{
  const h=harness();h.run('addToCart(1)');h.node('paid').value='500';let posts=0,payload,release;
  h.context.api=async(path,method,body)=>{if(method==='POST'){posts++;payload=body;await new Promise(r=>release=r);return{id:12,total:160};}throw Error('refresh offline');};
  h.run('showSuccessModal = () => {};');
  const first=h.run('completeSale()');await h.run('completeSale()');assert.equal(posts,1);
  release();await first;
  assert.equal(payload.paid_amount,160);assert.equal(payload.items[0].discount,40);
  assert.equal(h.run('cart.length'),0);assert.equal(h.run('allBooks[0].shop_quantity'),2);
  await h.run('completeSale()');assert.equal(posts,1);
});

test('failed sale retains cart and payment so cashier can correct the issue',async()=>{
  const h=harness();h.run('addToCart(1)');h.node('paid').value='160';
  h.context.api=async()=>{throw {detail:'Stock changed'};};await h.run('completeSale()');
  assert.equal(h.run('cart.length'),1);assert.equal(h.node('paid').value,'160');
  assert.equal(h.run('saleSubmitting'),false);assert.equal(h.node('checkout-error').textContent,'Stock changed');
});
