import { ConversationView } from "../../../components/conversation-view";

export default async function ChatPage({
  params,
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const { conversationId } = await params;
  return (
    <main className="flex min-h-[calc(100dvh-3.5rem)] w-full max-w-none flex-col p-0 md:min-h-dvh">
      <h1 className="sr-only">Chat</h1>
      <ConversationView key={conversationId} conversationId={conversationId} />
    </main>
  );
}
