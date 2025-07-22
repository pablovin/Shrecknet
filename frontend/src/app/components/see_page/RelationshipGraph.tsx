"use client";
import dynamic from "next/dynamic";

// ForceGraph2D requires browser globals like AFRAME which are
// not available during server side rendering. Dynamically load
// the component with SSR disabled to avoid "AFRAME is not defined"
// errors when Next.js builds the server bundle.
const ForceGraph2D = dynamic(
  async () => {
    if (typeof window !== "undefined") {
      if (!(window as any).AFRAME) {
        (window as any).AFRAME = {
          registerComponent: () => {},
          registerPrimitive: () => {},
          registerSystem: () => {},
          components: {},
          systems: {},
          primitives: {},
          utils: { diff: () => ({}) },
        } as any;
      }

      if (!(window as any).THREE) {
        const THREE = await import("three");
        (window as any).THREE = THREE;
      }
    }

    const mod = await import("react-force-graph");
    return mod.ForceGraph2D;
  },
  { ssr: false },
);

interface Node {
  id: number;
  name: string;
  logo?: string;
}

interface LinkInfo {
  id: number;
  source: number;
  target: number;
  type: string;
  direction: string;
  description?: string;
}

export default function RelationshipGraph({ nodes, links }: { nodes: Node[]; links: LinkInfo[] }) {
  const graphData = {
    nodes: nodes.map((n) => ({ id: n.id, name: n.name, logo: n.logo })),
    links: links.map((l) => ({
      id: l.id,
      source: l.direction === "incoming" ? l.target : l.source,
      target: l.direction === "incoming" ? l.source : l.target,
      type: l.type,
      description: l.description,
    })),
  };
  return (
    <div className="h-96 w-full">
      {/* ForceGraph2D may be missing if dependencies are not installed */}
      {/* @ts-ignore */}
      <ForceGraph2D
        graphData={graphData}
        nodeLabel={(node: any) => node.name}
        linkDirectionalArrowLength={6}
        linkLabel={(link: any) => link.type}
        onNodeClick={(node: any) => {
          window.location.href = `/pages/${node.id}`;
        }}
      />
    </div>
  );
}
