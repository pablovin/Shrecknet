import assert from 'node:assert/strict';
import test from 'node:test';
import {companionMarkdown} from '../scripts/apps/companion-markdown.js';

test('Companion Markdown renders headings, paragraphs, lists and tables',()=>{
 const html=companionMarkdown('# Rules\n\n**Choose** your edition.\n\n1. Read\n2. Create\n\n| Field | Value |\n| --- | --- |\n| HP | 0 |');
 assert.match(html,/<h1>Rules<\/h1>/);
 assert.match(html,/<strong>Choose<\/strong>/);
 assert.match(html,/<ol><li>Read<\/li><li>Create<\/li><\/ol>/);
 assert.match(html,/<td>HP<\/td><td>0<\/td>/);
});
test('Model HTML, images, links and code cannot execute or make network requests',()=>{
 const html=companionMarkdown('<img src=x onerror=alert(1)>\n\n[link](javascript:alert(1)) ![image](https://evil.test/img)\n\n`<script>`\n\n```\n<script>evil()</script>\n```');
 assert.ok(!html.includes('<img')&&!html.includes('<script>')&&!html.includes('<a '));
 assert.match(html,/&lt;img/);assert.match(html,/<code>&lt;script&gt;<\/code>/);
 assert.match(html,/<pre><code>&lt;script&gt;evil\(\)&lt;\/script&gt;<\/code><\/pre>/);
});
test('Markdown supports unordered lists, quotes and keeps incomplete tables readable',()=>{
 const html=companionMarkdown('- One\n- Two\n\n> A perspective\n\nA | B\n-- | no\n\n**uncertain');
 assert.match(html,/<ul><li>One<\/li><li>Two<\/li><\/ul>/);
 assert.match(html,/<blockquote>A perspective<\/blockquote>/);
 assert.ok(!html.includes('<table>')&&html.includes('**uncertain'));
});
