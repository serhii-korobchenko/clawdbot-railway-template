import assert from "node:assert/strict";
import { extractInstagramUrl, extractReelUrl, formatReelAnalysis, shouldHandleReel } from "../reel_telegram/src/routing.js";

assert.equal(extractReelUrl("https://www.instagram.com/reel/Dc6Ar0BNoLx/?stkn=abc"), "https://www.instagram.com/reel/Dc6Ar0BNoLx/");
assert.equal(extractReelUrl("https://www.oginstagram.com/reel/Dc6Ar0BNoLx/?stkn=abc"), "https://www.instagram.com/reel/Dc6Ar0BNoLx/");
assert.equal(extractReelUrl("https://instagram.com/p/abc/"), null);

assert.equal(extractInstagramUrl("https://www.oginstagram.com/p/DdBXy1rk9uA/?img_index=2&stkn=abc"), "https://www.instagram.com/p/DdBXy1rk9uA/?img_index=2");
assert.equal(extractInstagramUrl("https://instagram.com/p/DdBXy1rk9uA/"), "https://www.instagram.com/p/DdBXy1rk9uA/");

const reelSession = "agent:main:telegram:group:-1003804919781:topic:1570";
const prorokSession = "agent:main:telegram:group:-1003804919781:topic:112";

assert.equal(shouldHandleReel({channel:"telegram",sessionKey:reelSession,content:"https://instagram.com/reel/ABC_123/"}), true);
assert.equal(shouldHandleReel({channel:"telegram",sessionKey:prorokSession,content:"https://instagram.com/reel/ABC_123/"}), false);
assert.equal(shouldHandleReel({channel:"discord",sessionKey:reelSession,content:"https://instagram.com/reel/ABC_123/"}), false);
assert.equal(shouldHandleReel({channel:"telegram",sessionKey:"agent:main:telegram:group:-1003804919781:topic:11570",content:"https://instagram.com/reel/ABC_123/"}), false);

assert.equal(shouldHandleReel({channel:"telegram",sessionKey:reelSession,content:"https://www.oginstagram.com/p/DdBXy1rk9uA/?img_index=2"}), true);

const rendered = formatReelAnalysis({title:"Test Reel",transcript:"Transcript text.",visual_facts:"- Visual fact"});
assert.match(rendered, /Аналіз Reel/);
assert.match(rendered, /Transcript text/);
assert.match(rendered, /Visual fact/);
console.log("PASS");
