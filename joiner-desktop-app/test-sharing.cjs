const {test} = require('node:test');
const assert = require('node:assert/strict');
const {gzipSync} = require('node:zlib');
const {createHash} = require('node:crypto');
const {decodeShare} = require('./dist/main/sharing');
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
