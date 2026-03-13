import { notFound, redirect } from "next/navigation";
import { ChatShell } from "@/features/chat/components/chat-shell";
import { McpManager } from "@/features/tools/components/tools-manager";
import { WorkspaceHome } from "@/features/workspace/components/workspace-home";
import { WorkspacePlaceholderPage } from "@/features/workspace/components/workspace-placeholder-page";
import { workspaceRouteMeta } from "@/features/workspace/config/navigation";

type CatchAllPageProps = {
  params: Promise<{
    slug?: string[];
  }>;
};

export default async function CatchAllPage({ params }: CatchAllPageProps) {
  const { slug } = await params;

  if (!slug || slug.length === 0) {
    redirect("/dashboard");
  }

  const pathname = `/${slug.join("/")}`;

  if (pathname === "/tools") {
    redirect("/mcp");
  }

  const route = workspaceRouteMeta[pathname];

  if (!route) {
    notFound();
  }

  if (pathname === "/dashboard") {
    return <WorkspaceHome />;
  }

  if (pathname === "/chat") {
    return <ChatShell />;
  }

  if (pathname === "/mcp") {
    return <McpManager />;
  }

  return (
    <WorkspacePlaceholderPage title={route.title} description={route.description} />
  );
}
