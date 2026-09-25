import assert from "node:assert/strict";
import { extractReelUrl, formatReelAnalysis, shouldHandleReel } from "../reel_telegram/src/routing.js";

assert.equal(extractReelUrl("https://www.instagram.com/reel/Dc6Ar0BNoLx/?stkn=abc"), "https://www.instagram.com/reel/Dc6Ar0BNoLx/");
assert.equal(extractReelUrl("https://www.oginstagram.com/reel/Dc6Ar0BNoLx/?stkn=abc"), "https://www.instagram.com/reel/Dc6Ar0BNoLx/");
assert.equal(extractReelUrl("https://instagram.com/p/abc/"), null);

const reelSession = "agent:main:telegram:group:-1003804919781:topic:1570";
const prorokSession = "agent:main:telegram:group:-1003804919781:topic:112";

assert.equal(shouldHandleReel({channel:"telegram",sessionKey:reelSession,content:"https://instagram.com/reel/ABC_123/"}), true);
assert.equal(shouldHandleReel({channel:"telegram",sessionKey:prorokSession,content:"https://instagram.com/reel/ABC_123/"}), false);
assert.equal(shouldHandleReel({channel:"discord",sessionKey:reelSession,content:"https://instagram.com/reel/ABC_123/"}), false);
assert.equal(shouldHandleReel({channel:"telegram",sessionKey:"agent:main:telegram:group:-1003804919781:topic:11570",content:"https://instagram.com/reel/ABC_123/"}), false);

const rendered = formatReelAnalysis({title:"Test Reel",transcript:"Transcript text.",visual_facts:"- Visual fact"});
assert.match(rendered, /Аналіз Reel/);
assert.match(rendered, /Transcript text/);
assert.match(rendered, /Visual fact/);
console.log("PASS");
