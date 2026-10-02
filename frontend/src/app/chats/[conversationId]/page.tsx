import { ConversationView } from "../../../components/conversation-view";

export default async function ChatPage({
  params,
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const { conversationId } = await params;
  return (
    <main className="flex min-h-[calc(100dvh-3.5rem)] w-full max-w-3xl xl:max-w-5xl flex-col pt-10 pb-0 md:min-h-dvh md:pt-20">
      <h1 className="sr-only">Chat</h1>
      <ConversationView key={conversationId} conversationId={conversationId} />
    </main>
  );
}
