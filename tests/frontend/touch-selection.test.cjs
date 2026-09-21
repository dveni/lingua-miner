const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const modulePath = path.resolve(__dirname, '../../static/touch-selection.js');
const api = fs.existsSync(modulePath) ? require(modulePath) : {};

test('structured range preserves Unicode, punctuation and whitespace in either direction', () => {
  assert.equal(typeof api.phrase, 'function');
  const tokens = [{t:'d',ws:''},{t:'els',ws:' '},{t:'“',ws:''},{t:'café',ws:''},{t:'”',ws:''},{t:',',ws:'\n'},{t:'世界',ws:''}];
  assert.equal(api.phrase(tokens, 0, 6), 'dels “café”,\n世界');
  assert.equal(api.phrase(tokens, 6, 0), 'dels “café”,\n世界');
  assert.equal(api.phrase(tokens, 2, 4), '“café”');
  assert.equal(api.phrase([{t:'Hola',is_word:true},{t:',',is_word:false},{t:'món',is_word:true}], 0, 2), 'Hola, món');
});

class Target {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, fn, options) { const list = this.listeners.get(type) || []; list.push({fn, options}); this.listeners.set(type, list); }
  removeEventListener(type, fn) { this.listeners.set(type, (this.listeners.get(type) || []).filter(x => x.fn !== fn)); }
  emit(type, props = {}) {
    const ev = {type, target:this, cancelable:true, defaultPrevented:false, preventDefault(){this.defaultPrevented=true;}, stopImmediatePropagation(){this.stopped=true;}, stopPropagation(){this.stopped=true;}, ...props};
    for (const {fn} of [...(this.listeners.get(type) || [])]) { fn(ev); if (ev.stopped) break; }
    return ev;
  }
}
function harness(onInteraction) {
  let now = 0, serial = 0;
  const timers = new Map(), observers = [];
  const win = new Target(), doc = new Target();
  win.setTimeout = (fn, ms) => { timers.set(++serial, {fn, at:now+ms}); return serial; };
  win.clearTimeout = id => timers.delete(id);
  win.Date = {now:() => now};
  win.MutationObserver = class { constructor(fn){this.fn=fn;observers.push(this);} observe(){this.on=true;} disconnect(){this.on=false;} };
  doc.defaultView = win; doc.documentElement = {}; doc.hidden = false;
  const classes = () => { const s = new Set(); return {add:x=>s.add(x),remove:x=>s.delete(x),contains:x=>s.has(x),toggle:(x,v)=>v?s.add(x):s.delete(x)}; };
  const container = new Target(); container.ownerDocument = doc; container.classList=classes(); container.isConnected=true;
  const tokens = [{t:'Bon',ws:' '},{t:'dia',ws:''},{t:',',ws:' '},{t:'món',ws:''}];
  const nodes = tokens.map((t,i) => ({dataset:{tokenIndex:String(i)},classList:classes(),isConnected:true,textContent:t.t,closest(){return this;}}));
  container.contains = node => nodes.includes(node) && node.isConnected;
  container.querySelectorAll = () => nodes;
  doc.elementFromPoint = x => nodes[Math.floor(x/20)] || null;
  const selected = [], interaction = [];
  const control = api.bind(container, {tokens, onSelect:(...args)=>selected.push(args), onInteraction:x=>{interaction.push(x);onInteraction?.(x);}});
  const touch = (i=0,x=1) => ({identifier:7,clientX:x,clientY:1,target:nodes[i]});
  return {container,nodes,doc,win,selected,interaction,control,observers,timers,touch,
    start(i=0) { const t=touch(i,i*20+1); return container.emit('touchstart',{target:nodes[i],touches:[t],changedTouches:[t]}); },
    move(x) { const t=touch(0,x); return doc.emit('touchmove',{touches:[t],changedTouches:[t]}); },
    end(x=1) { return doc.emit('touchend',{touches:[],changedTouches:[touch(0,x)]}); },
    tick(ms) { now+=ms; for(const [id,t] of [...timers]) if(t.at<=now){timers.delete(id);t.fn();} }
  };
}

test('long press activates at 420ms and drag selects structured reverse range on release', () => {
  assert.equal(typeof api.bind, 'function');
  const h=harness(); h.start(3);
  assert.deepEqual(h.interaction,[true]);
  h.tick(419); assert.equal(h.nodes[3].classList.contains('touch-expression-selected'),false);
  h.tick(1); assert.equal(h.nodes[3].classList.contains('touch-expression-selected'),true);
  const move=h.move(1); assert.equal(move.defaultPrevented,true);
  assert.equal(h.doc.listeners.get('touchmove')[0].options.passive,false);
  assert.equal(h.nodes.every(n=>n.classList.contains('touch-expression-selected')),true);
  h.end(1);
  assert.equal(h.selected[0][0],'Bon dia, món'); assert.equal(h.selected[0][1],h.nodes[3]);
  assert.deepEqual(h.interaction,[true,false]);
  assert.equal(h.nodes.some(n=>n.classList.contains('touch-expression-selected')),false);
});

test('preactivation slop cancels without blocking natural scrolling; quick taps stay native', () => {
  const h=harness(); h.start();
  assert.equal(h.move(13).defaultPrevented,false); h.tick(500); h.end();
  assert.deepEqual(h.interaction,[true,false]); assert.deepEqual(h.selected,[]);
  h.start(); h.tick(80); assert.equal(h.end().defaultPrevented,false);
  assert.equal(h.container.emit('click',{target:h.nodes[0],detail:1}).defaultPrevented,false);
  assert.deepEqual(h.selected,[]);
});

test('touch compatibility hover and one synthetic gesture click are suppressed, not desktop or next tap', () => {
  const h=harness();
  assert.equal(h.control.ignoreHover({}),false);
  h.start(); h.tick(420); h.end();
  assert.equal(h.control.ignoreHover({}),true);
  assert.equal(h.container.emit('click',{target:h.nodes[0],detail:0}).defaultPrevented,false);
  assert.equal(h.container.emit('click',{target:h.nodes[0],detail:1,sourceCapabilities:{firesTouchEvents:false}}).defaultPrevented,false);
  assert.equal(h.container.emit('click',{target:h.nodes[0],detail:1,sourceCapabilities:{firesTouchEvents:true}}).defaultPrevented,true);
  assert.equal(h.container.emit('click',{target:h.nodes[0],detail:1}).defaultPrevented,false);
  h.start(); h.tick(420); h.end(); h.start(); h.end();
  assert.equal(h.container.emit('click',{target:h.nodes[0],detail:1}).defaultPrevented,false);
  h.tick(1100); assert.equal(h.control.ignoreHover({}),false);
  assert.equal(h.control.ignoreHover({sourceCapabilities:{firesTouchEvents:true}}),true);
});

test('native selection/context menu suppression applies only to touched tokens and resets for mouse', () => {
  const h=harness();
  assert.equal(h.container.emit('contextmenu',{target:h.nodes[0]}).defaultPrevented,false);
  h.start();
  assert.equal(h.container.classList.contains('touch-selection-touch'),true);
  assert.equal(h.container.emit('contextmenu',{target:h.nodes[0]}).defaultPrevented,true);
  assert.equal(h.container.emit('contextmenu',{target:h.container}).defaultPrevented,false);
  h.end(); h.container.emit('pointerdown',{pointerType:'mouse',target:h.nodes[0]});
  assert.equal(h.container.classList.contains('touch-selection-touch'),false);
  assert.equal(h.control.ignoreHover({}),false);
  assert.equal(h.container.emit('contextmenu',{target:h.nodes[0]}).defaultPrevented,false);
});

function appFunctions(extra = {}) {
  const source=fs.readFileSync(path.resolve(__dirname,'../../static/app.js'),'utf8');
  const context={esc:x=>String(x),segRecTier:()=>0,isRecWord:()=>false,stOf:()=> 'known', ...extra};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function reconstructWs('),source.indexOf('function scheduleClose(')),context);
  return context;
}

test('token rendering exposes structured indices without making punctuation clickable', () => {
  const ctx=appFunctions();
  const html=ctx.tokenHtml({text:'Bon dia, món',tokens:[{t:'Bon',lemma:'bon',is_word:true},{t:'dia',lemma:'dia',is_word:true},{t:',',is_word:false},{t:'món',lemma:'món',is_word:true}]});
  assert.deepEqual([...html.matchAll(/data-token-index="(\d+)"/g)].map(x=>x[1]),['0','1','2','3']);
  assert.equal((html.match(/class="t /g)||[]).length,3);
});

test('app integration preserves desktop native selection, forwards touch phrases, and publishes pending interaction', () => {
  const h=harness(), calls=[], events=[]; let options, destroyed=0, ignored=true;
  const seg={tokens:[{t:'Bon',ws:' '},{t:'dia',ws:''}]};
  const ctx=appFunctions({
    TouchSelection:{bind:(container,opts)=>{options=opts;return {destroy:()=>destroyed++,ignoreHover:()=>ignored};}},
    SEGS:[seg],window:{getSelection:()=>({toString:()=> 'native phrase'})},
    document:{dispatchEvent:ev=>events.push(ev)},CustomEvent:class {constructor(type,opts){this.type=type;this.detail=opts.detail;}},
    openPopup:(...args)=>calls.push(args),clearTimeout:()=>{},setTimeout:()=>{throw Error('touch must not schedule hover');},HOVER_TIMER:null,CLOSE_TIMER:null,PINNED:false,HOVER:null,
  });
  ctx.bindTokenEvents(h.container,0);
  assert.ok(options,'touch binding exists'); assert.equal(options.tokens,seg.tokens);
  h.nodes[0].onclick({stopPropagation(){}});
  assert.deepEqual(calls[0],[0,'native phrase',h.nodes[0],true]);
  options.onSelect('Bon dia',h.nodes[0]); assert.deepEqual(calls[1],[0,'Bon dia',h.nodes[0],true]);
  h.nodes[0].onmouseenter({}); assert.equal(ctx.HOVER,null);
  options.onInteraction(true); options.onInteraction(false);
  assert.equal(events[0].type,'expression-interaction'); assert.equal(events[0].detail.active,true); assert.equal(events[1].detail.active,false);
  h.nodes[0].isConnected=false; options.onSelect('stale',h.nodes[0]); assert.equal(calls.length,2);
  ctx.bindTokenEvents(h.container,0); assert.equal(destroyed,1);
});

test('selection CSS is touch scoped and leaves status/recommendation backgrounds and borders intact', () => {
  const css=fs.readFileSync(path.resolve(__dirname,'../../static/style.css'),'utf8');
  const block=css.split('/* Touch expression selection */')[1]?.split('/* End touch expression selection */')[0];
  assert.ok(block,'selection CSS block exists');
  assert.match(block,/\.touch-selection-touch\s+\.t\s*\{[^}]*user-select:\s*none/s);
  assert.match(block,/\.touch-expression-selected\s*\{[^}]*outline:/s);
  assert.doesNotMatch(block,/background\s*:|border(?:-bottom)?\s*:|touch-action\s*:/);
});

test('normal Chrome pointerup then implicit capture loss still commits at touchend', () => {
  const h=harness(); h.start(); h.tick(420); h.move(61);
  h.doc.emit('pointerup',{pointerType:'touch'});
  h.doc.emit('lostpointercapture',{pointerType:'touch'});
  assert.deepEqual(h.interaction,[true]);
  h.end(61); assert.equal(h.selected[0][0],'Bon dia, món');
});

test('hit testing cannot cross subtitles or accept malformed token indices', () => {
  const h=harness(); h.start(); h.tick(420); h.move(21);
  const foreign={dataset:{tokenIndex:'3'},closest(){return this;}};
  h.doc.elementFromPoint=()=>foreign; h.move(200);
  h.doc.elementFromPoint=()=>h.nodes[3]; h.nodes[3].dataset.tokenIndex='3junk'; h.move(61);
  h.end(); assert.equal(h.selected[0][0],'Bon dia');
  const detached=harness();detached.start();detached.tick(420);detached.nodes[0].isConnected=false;detached.end();
  assert.deepEqual(detached.selected,[]);
});

test('classic script exposes the same public API without CommonJS', () => {
  const context={};vm.createContext(context);vm.runInContext(fs.readFileSync(modulePath,'utf8'),context);
  assert.equal(typeof context.TouchSelection.bind,'function');
  assert.equal(typeof context.TouchSelection.phrase,'function');
});

test('synchronous lifecycle teardown from interaction callback cannot leave a timer or crash', () => {
  const h=harness(active=>{if(active)h.control.destroy();});
  assert.doesNotThrow(()=>h.start()); h.tick(500);
  assert.equal(h.timers.size,0); assert.deepEqual(h.interaction,[true,false]);
});

test('cancellation and lifecycle teardown release pending and active interactions', () => {
  const cancels = [
    h=>h.doc.emit('touchcancel'), h=>h.doc.emit('keydown',{key:'Escape'}),
    h=>h.doc.emit('lostpointercapture',{pointerType:'touch'}), h=>h.doc.emit('pointercancel',{pointerType:'touch'}),
    h=>h.doc.emit('expression-reset'),
    h=>h.win.emit('blur'), h=>h.win.emit('pagehide'),
    h=>{h.doc.hidden=true;h.doc.emit('visibilitychange');},
    h=>{h.nodes[0].isConnected=false;for(const o of h.observers)if(o.on)o.fn();},
    h=>h.control.destroy(),
    h=>h.doc.emit('touchstart',{touches:[h.touch(),{...h.touch(),identifier:8}]}),
  ];
  for(const cancel of cancels) for(const active of [false,true]) {
    const h=harness(); h.start(); if(active)h.tick(420); cancel(h); h.tick(500); h.end();
    assert.deepEqual(h.selected,[],String(cancel));
    assert.deepEqual(h.interaction,[true,false],String(cancel));
    assert.equal(h.timers.size,0);
    assert.equal(h.observers.some(o=>o.on),false);
    assert.equal(h.nodes.some(n=>n.classList.contains('touch-expression-selected')),false);
    assert.equal((h.doc.listeners.get('touchmove')||[]).length,0);
  }
});
