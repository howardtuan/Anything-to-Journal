import assert from "node:assert/strict";
import test from "node:test";
import "../skills/anything-to-journal/assets/workspace/evidence.js";

const { normalizedText, textIndex, findInIndex, matchesOnPage, locate, normalizedRect } = globalThis.WorkspaceEvidenceMatchers;
const item = (str, x = 0, y = 100, width = str.length * 6, hasEOL = false) => ({ str, transform: [12, 0, 0, 12, x, y], width, hasEOL });
const page = (number, items) => ({ number, items, index: textIndex(items), wrappedIndex: textIndex(items, true) });

test("matches wrapped PDF text and keeps original item offsets for highlights", () => {
  const p = page(1, [item("The model achieved an", 0, 100, 130, true), item("accuracy of 0.9.", 0, 85)]);
  const match = locate([p], { text: "achieved an accuracy of 0.9" });
  assert.equal(match.page.number, 1);
  assert.deepEqual(match.groups, [{ item: 0, start: 10, end: 21 }, { item: 1, start: 0, end: 15 }]);
});

test("joins fragmented words and removes only line-end hyphens", () => {
  const p = page(1, [item("accu", 0, 100, 24), item("racy", 24, 100, 24), item("improve-", 0, 85, 48, true), item("ment", 0, 70)]);
  assert.equal(matchesOnPage(p, "accuracy improvement").length, 1);
  assert.equal(matchesOnPage(page(1, [item("held-out accuracy")]), "heldout").length, 0);
});

test("repeated values require page or occurrence; never silently choose the first", () => {
  const pages = [page(1, [item("Accuracy 0.9. Recall 0.9.")]), page(2, [item("Accuracy 0.9.")])];
  assert.match(locate(pages, { text: "0.9" }).reason, /3 times/);
  assert.equal(locate(pages, { text: "0.9", page: 2 }).page.number, 2);
  assert.equal(locate(pages, { text: "0.9", occurrence: 3 }).page.number, 2);
  assert.equal(locate(pages, { text: "0.9", page: 1, occurrence: 2 }).groups[0].start, 21);
  assert.match(locate(pages, { text: "0.9", occurrence: 4 }).reason, /not found/);
});

test("a shorter value or term cannot be mistaken for a different metric", () => {
  const index = textIndex([item("10.9 0.90 0.9 accuracy inaccuracy")]);
  assert.equal(findInIndex(index, "0.9").length, 1);
  assert.equal(findInIndex(index, "accuracy").length, 1);
});

test("source phrases can match within Chinese prose without spaces", () => {
  const p = page(1, [item("本模型準確率為0.9，測試完成。")]);
  const match = locate([p], { text: "模型準確率為0.9" });
  assert.equal(match.page.number, 1);
  assert.equal(match.groups[0].start, 1);
});

test("ligatures, nonbreaking spaces and soft hyphens normalize without losing offsets", () => {
  assert.equal(normalizedText("  ef\ufb01ciency\u00a0was\n0.9\u00ad  "), "efficiency was 0.9");
  const match = locate([page(1, [item("ef\ufb01ciency")])], { text: "efficiency" });
  assert.deepEqual(match.groups, [{ item: 0, start: 0, end: 9 }]);
  assert.equal(matchesOnPage(page(1, [item("Accuracy 0.9")]), "Accuracy 0.9").length, 1);
});

test("normalized regions are bounded and need an explicit PDF page", () => {
  const rect = [0.1, 0.2, 0.6, 0.3];
  assert.deepEqual(normalizedRect(rect), rect);
  for (const bad of [[-0.1, 0, 1, 1], [0.8, 0, 0.3, 1], [0, 0, 0, 1], [0, 0, NaN, 1], ["0", 0, 1, 1]]) assert.equal(normalizedRect(bad), null);
  const pages = [page(1, [])];
  assert.equal(locate(pages, { page: 1, rect }).explicitRegion, true);
  assert.match(locate(pages, { rect }).reason, /No precise/);
  assert.match(locate(pages, { page: 2, rect }).reason, /outside/);
});

test("a missing quotation or page-only locator never produces an invented highlight", () => {
  const pages = [page(1, [item("Model accuracy was 0.9.")])];
  assert.match(locate(pages, { text: "accuracy was 0.8" }).reason, /could not be matched/);
  assert.match(locate(pages, { page: 1 }).reason, /no exact passage/);
});
