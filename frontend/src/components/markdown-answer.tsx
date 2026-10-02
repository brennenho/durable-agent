import Markdown from "react-markdown";

export function MarkdownAnswer({ text }: { text: string }) {
  return (
    <div className="markdown-answer">
      <Markdown
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          // Answers must not fetch remote images automatically.
          img: ({ alt }) => <span>{alt}</span>,
        }}
      >
        {text}
      </Markdown>
    </div>
  );
}
