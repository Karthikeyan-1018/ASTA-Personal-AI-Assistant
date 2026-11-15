import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { Check, Copy } from 'lucide-react';

interface CodeBlockProps {
  language: string;
  value: string;
}

const CodeBlock: React.FC<CodeBlockProps> = ({ language, value }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback or ignore
    }
  };

  return (
    <div className="relative my-3 overflow-hidden rounded-xl border border-slate-800 bg-slate-950 text-left font-mono text-xs">
      <div className="flex items-center justify-between border-b border-slate-800 bg-slate-900/90 px-3.5 py-1.5 text-[11px] text-slate-400">
        <span className="font-semibold uppercase tracking-wider text-indigo-300">
          {language || 'text'}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className="flex items-center gap-1 rounded px-2 py-0.5 text-slate-400 transition hover:bg-slate-800 hover:text-slate-200"
          title="Copy code"
        >
          {copied ? (
            <>
              <Check className="h-3.5 w-3.5 text-emerald-400" />
              <span className="text-emerald-400">Copied!</span>
            </>
          ) : (
            <>
              <Copy className="h-3.5 w-3.5" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <div className="overflow-x-auto p-3">
        <SyntaxHighlighter
          language={language || 'text'}
          style={vscDarkPlus}
          customStyle={{
            margin: 0,
            padding: 0,
            background: 'transparent',
            fontSize: '12px',
            lineHeight: '1.5',
          }}
          wrapLongLines={true}
        >
          {value}
        </SyntaxHighlighter>
      </div>
    </div>
  );
};

export interface MarkdownRendererProps {
  content: string;
}

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({ content }) => {
  return (
    <div className="prose prose-invert max-w-none text-xs sm:text-sm leading-relaxed">
      <ReactMarkdown
        components={{
          code({ className, children, ...props }) {
            const match = /language-(\w+)/.exec(className || '');
            const isInline = !match && !String(children).includes('\n');
            if (isInline) {
              return (
                <code
                  className="rounded bg-slate-800/80 px-1.5 py-0.5 font-mono text-xs text-indigo-300 border border-slate-700/50"
                  {...props}
                >
                  {children}
                </code>
              );
            }
            return (
              <CodeBlock
                language={match ? match[1] : ''}
                value={String(children).replace(/\n$/, '')}
              />
            );
          },
          h1: ({ children }) => (
            <h1 className="mt-3 mb-2 text-lg font-bold text-white">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="mt-3 mb-2 text-base font-bold text-white">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="mt-2.5 mb-1.5 text-sm font-semibold text-indigo-300">{children}</h3>
          ),
          p: ({ children }) => <p className="mb-2.5 last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="mb-2.5 list-disc pl-5 space-y-1">{children}</ul>,
          ol: ({ children }) => <ol className="mb-2.5 list-decimal pl-5 space-y-1">{children}</ol>,
          li: ({ children }) => <li className="text-slate-300">{children}</li>,
          blockquote: ({ children }) => (
            <blockquote className="my-2 border-l-2 border-indigo-500 bg-slate-900/60 pl-3 py-1 text-slate-300 italic">
              {children}
            </blockquote>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
};

export default MarkdownRenderer;
