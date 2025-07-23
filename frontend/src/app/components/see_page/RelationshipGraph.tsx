"use client";
import { useState } from "react";
import { GraphCanvas, lightTheme, GraphNode, GraphEdge } from "reagraph";

interface NodeInfo {
  id: string;
  name: string;
  logo?: string;
  color: string;
  description?: string;
  world_id?: number;
  concept_id?: number;
  moreCount?: number;
}

interface LinkInfo {
  id: string;
  source: string;
  target: string;
  label: string;
}

export default function RelationshipGraph({
  nodes,
  links,
}: {
  nodes: NodeInfo[];
  links: LinkInfo[];
}) {
  const [hover, setHover] = useState<{
    node: NodeInfo;
    x: number;
    y: number;
  } | null>(null);

  const graphNodes: GraphNode<NodeInfo>[] = nodes.map((n) => ({
    id: n.id,
    label: n.name,
    icon: n.logo,
    fill: n.color,
    data: n,
  }));
  const graphEdges: GraphEdge[] = links.map((l) => ({
    id: l.id,
    source: l.source,
    target: l.target,
    label: l.label,
  }));

  return (
    <div className="relative h-96 w-full">
      <GraphCanvas
        nodes={graphNodes}
        edges={graphEdges}
        theme={lightTheme}
        labelType="all"
        onNodePointerOver={(node, event) => {
          setHover({
            node: node.data as NodeInfo,
            x: event.clientX,
            y: event.clientY,
          });
        }}
        onNodePointerOut={() => setHover(null)}
        onNodeClick={(node) => {
          const data = node.data as NodeInfo;
          if (data.world_id && data.concept_id) {
            window.location.href = `/worlds/${data.world_id}/concept/${data.concept_id}/page/${node.id}`;
          } else {
            window.location.href = `/pages/${node.id}`;
          }
        }}
      />
      {hover && (
        <div
          className="pointer-events-none absolute z-10 bg-[var(--surface)] border border-[var(--border)] rounded shadow-md p-2 text-sm"
          style={{ left: hover.x + 10, top: hover.y + 10 }}
        >
          <div className="font-semibold mb-1 flex items-center gap-2">
            {hover.node.logo && (
              <img
                src={hover.node.logo}
                alt="logo"
                className="w-4 h-4 object-contain"
              />
            )}
            {hover.node.name}
          </div>
          {hover.node.description && (
            <div className="text-xs max-w-xs line-clamp-3">
              {hover.node.description}
            </div>
          )}
          {hover.node.moreCount && (
            <div className="text-[var(--muted-foreground)] text-xs mt-1">
              +{hover.node.moreCount} more relationships
            </div>
          )}
        </div>
      )}
    </div>
  );
}
