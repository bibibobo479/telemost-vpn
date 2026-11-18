const {test} = require('node:test');
const assert = require('node:assert/strict');
const {gzipSync} = require('node:zlib');
const {createHash} = require('node:crypto');
const {decodeShare, jsonRequest} = require('./dist/main/sharing');
function code(value) {
  const packed=gzipSync(Buffer.from(JSON.stringify(value))).toString('base64url');
  return 'TMVPN1.'+packed+'.'+createHash('sha256').update(packed).digest('hex').slice(0,12);
}
const fixture={version:1,token:'fixture-token-not-real',folder:'disk:/Телемост VPN',profile:{fps:90,batch:120,dual_track:true}};
test('Linux-compatible code supports fast dual profiles',()=>assert.deepEqual(decodeShare(code(fixture)),fixture));
test('Reject damaged, oversized, unsafe and wrongly typed codes',()=>{
  assert.throws(()=>decodeShare(code(fixture).slice(0,-2)));
  assert.throws(()=>decodeShare(code({...fixture,extra:'X'.repeat(100000)})));
  assert.throws(()=>decodeShare(code({...fixture,folder:'disk:/other'})));
  assert.throws(()=>decodeShare(code({...fixture,token:'bad\r\nheader'})));
  assert.throws(()=>decodeShare(code({...fixture,profile:{fps:true,batch:120,dual_track:false}})));
});
test('Follow signed storage redirects without OAuth; reject unsafe redirects and loops',async()=>{
  const original=global.fetch;
  try {
    const calls=[];
    global.fetch=async(url,options)=>{
      calls.push({url,options});
      return calls.length===1 ? new Response(null,{status:302,headers:{location:'https://mds.storage.yandex.net/file'}}) :
        new Response(JSON.stringify({link:'ok'}));
    };
    assert.deepEqual(await jsonRequest('https://downloader.disk.yandex.ru/file'),{link:'ok'});
    assert.equal(calls.length,2);
    assert.ok(calls.every(c=>c.options.headers===undefined));
    for(const location of ['http://mds.storage.yandex.net/file','https://evil.example/file']) {
      global.fetch=async()=>new Response(null,{status:302,headers:{location}});
      await assert.rejects(jsonRequest('https://downloader.disk.yandex.ru/file'));
    }
    let count=0;
    global.fetch=async()=>{count++;return new Response(null,{status:302,headers:{location:'https://mds.storage.yandex.net/file'}})};
    await assert.rejects(jsonRequest('https://cloud-api.yandex.net/file',{Authorization:'OAuth fixture'}));
    assert.equal(count,1);
    count=0;
    await assert.rejects(jsonRequest('https://downloader.disk.yandex.ru/file'));
    assert.equal(count,6);
  } finally { global.fetch=original; }
});
