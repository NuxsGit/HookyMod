"use strict";

// Name-based suggestions deliberately do not claim rig or game compatibility.
const assert = require("node:assert/strict");
const c = require("../hookycreator/web/player.js");
const asset = (kind, name) => ({ kind, name, rel: `Model/${name}` });
const models = [
  asset("model", "Player_Child.bfres"),
  asset("model", "Player_Child_Tunic.bfres"),
  asset("model", "Player_Child_Sword.bfres"),
  asset("model", "Armor_Child_Tunic.bfres"),
  asset("model", "Link_Child.bfres"),
  asset("model", "Player_Adult.bfres"),
  asset("model", "Armor_Adult_Tunic.bfres"),
  asset("model", "Player_Childish.bfres"),
  asset("model", "Armor_001_Upper.bfres"),
  asset("model", "Mob_Bat.bfres"),
  asset("model", "LinkAdult.bfres"),
];
const child = asset("animation", "Player_Child_Animation.anim.bfres.zs");
const adult = asset("animation", "Player_Adult.anim.bfres");
const other = asset("animation", "Unrelated.anim.bfres");
const assets = [...models, child, adult, other];

assert.equal(c.age(models[0]), "child");
assert.equal(c.age(models[10]), "adult");
assert.equal(c.age(models[3]), "", "armor is not assumed to be a base player");
assert.equal(c.markedAge(models[3]), "child");
assert.equal(c.category(models[2]), "objet");
assert.equal(c.category(models[3]), "tenue / skin");
assert.equal(c.category(models[8]), "tenue / skin");
assert.equal(c.category(models[9]), "modèle");
assert.deepEqual(c.models(assets, "child", "").map((a) => a.rel),
  [models[0].rel, models[1].rel, models[2].rel, models[4].rel]);
assert.deepEqual(c.models(assets, "adult", "link").map((a) => a.rel), [models[10].rel]);
assert.deepEqual(c.models(assets, "all", "mob").map((a) => a.rel), [models[9].rel]);
assert.ok(c.equipmentModels(assets, "suggested", "armor").includes(models[8]));
assert.ok(!c.equipmentModels(assets, "suggested", "mob").includes(models[9]));
assert.ok(c.equipmentModels(assets, "all", "mob").includes(models[9]));

const suggestions = c.suggestModels(assets, child.rel);
assert.equal(suggestions[0].asset.rel, models[0].rel, "exact names rank before variants");
const rels = suggestions.map((item) => item.asset.rel);
for (const expected of [models[1], models[2], models[3], models[4]]) assert.ok(rels.includes(expected.rel), expected.rel);
for (const excluded of [models[5], models[6], models[7], models[8], models[9]]) assert.ok(!rels.includes(excluded.rel), excluded.rel);
assert.equal(suggestions.find((item) => item.asset === models[3]).reason, "Âge indiqué dans le nom");
assert.equal(c.animationPriority(child, models[0]), 0);
assert.equal(c.animationPriority(adult, models[0]), 2);
assert.deepEqual(c.suggestModels(assets, "../unknown"), []);
assert.deepEqual(c.suggestModels(assets, other.rel), []);
assert.equal(c.animationKey(asset("animation", "Creature_Alpha_Player_Animation.anim.bfres")), "creature_alpha");
assert.equal(c.animationPriority(asset("animation", "_Animation.anim.bfres"), models[0]), 2,
  "empty name must not match every model");
console.log("Player catalog associations: OK");
