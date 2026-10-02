import { ConversationView } from "../../../components/conversation-view";

export default async function ChatPage({
  params,
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const { conversationId } = await params;
  return (
    <main className="w-full max-w-3xl pt-10 md:pt-20">
      <h1 className="sr-only">Chat</h1>
      <ConversationView key={conversationId} conversationId={conversationId} />
    </main>
  );
}
