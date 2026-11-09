// A deliberately small, inert Markdown renderer. Source/model HTML, links and
// images remain text; navigation and artwork use the authorized rich envelope.
import { escapeHtml } from './atlas-content.js';

function inline(value) {
  return String(value).split(/(`[^`\n]+`)/).map((part) => {
    if (part.startsWith('`') && part.endsWith('`')) return `<code>${escapeHtml(part.slice(1,-1))}</code>`;
    return escapeHtml(part).replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, '<em>$1</em>');
  }).join('');
}
function cells(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim());
}
export function companionMarkdown(value) {
  const lines=String(value ?? '').replace(/\r\n?/g,'\n').split('\n'), output=[];
  let index=0;
  while(index<lines.length) {
    const line=lines[index];
    if (!line.trim()) { index++; continue; }
    if (/^```/.test(line)) {
      const code=[]; index++;
      while(index<lines.length && !/^```/.test(lines[index])) code.push(lines[index++]);
      if(index<lines.length) index++;
      output.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`); continue;
    }
    const heading=/^(#{1,6})\s+(.+)$/.exec(line);
    if(heading) { output.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`); index++; continue; }
    if (line.includes('|') && index+1<lines.length && cells(lines[index+1]).every(cell=>/^:?-{3,}:?$/.test(cell)) && cells(line).length===cells(lines[index+1]).length) {
      const headers=cells(line), rows=[]; index+=2;
      while(index<lines.length && lines[index].includes('|') && cells(lines[index]).length===headers.length) rows.push(cells(lines[index++]));
      output.push(`<div class="shrecknet-companion-rich-table"><table><thead><tr>${headers.map(cell=>`<th>${inline(cell)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(cell=>`<td>${inline(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`); continue;
    }
    const item=/^\s*(?:([-+*])|\d+\.)\s+(.+)$/.exec(line);
    if(item) {
      const tag=item[1]?'ul':'ol', items=[];
      while(index<lines.length) {
        const next=/^\s*(?:([-+*])|\d+\.)\s+(.+)$/.exec(lines[index]);
        if(!next || (next[1]?'ul':'ol')!==tag) break;
        items.push(`<li>${inline(next[2])}</li>`); index++;
      }
      output.push(`<${tag}>${items.join('')}</${tag}>`); continue;
    }
    if(/^>\s?/.test(line)) {
      const quote=[];
      while(index<lines.length && /^>\s?/.test(lines[index])) quote.push(inline(lines[index++].replace(/^>\s?/,'')));
      output.push(`<blockquote>${quote.join('<br>')}</blockquote>`); continue;
    }
    const paragraph=[inline(line)]; index++;
    while(index<lines.length && lines[index].trim() && !/^(?:#{1,6}\s|```|>\s?|\s*(?:[-+*]|\d+\.)\s)/.test(lines[index]) &&
      !(lines[index].includes('|') && index+1<lines.length && cells(lines[index+1]).every(cell=>/^:?-{3,}:?$/.test(cell)))) paragraph.push(inline(lines[index++]));
    output.push(`<p>${paragraph.join('<br>')}</p>`);
  }
  return output.join('');
}
