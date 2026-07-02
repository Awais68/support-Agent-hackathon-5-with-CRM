'use client';

import { useEffect, useRef } from 'react';
import { TicketDetail, Message, connectTicketWebSocket } from '@/lib/api';

interface TicketStatusProps {
  ticket: TicketDetail;
  onRefresh: () => void;
  isRefreshing: boolean;
}

const statusColors: Record<string, string> = {
  open: 'bg-blue-100 text-blue-800 border-blue-300',
  in_progress: 'bg-yellow-100 text-yellow-800 border-yellow-300',
  resolved: 'bg-green-100 text-green-800 border-green-300',
  escalated: 'bg-red-100 text-red-800 border-red-300',
  closed: 'bg-gray-100 text-gray-800 border-gray-300',
};

const channelLabels: Record<string, string> = {
  email: '📧 Email',
  whatsapp: '💬 WhatsApp',
  web: '🌐 Web',
};

function formatDate(isoDate: string): string {
  const date = new Date(isoDate);
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  } as any);
}

export default function TicketStatus({ ticket, onRefresh, isRefreshing }: TicketStatusProps) {
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout>;
    let isMounted = true;

    function connect() {
      const ticketId = (ticket as any).id || ticket.ticket_id;
      if (!ticketId) return;
      const ws = connectTicketWebSocket(ticketId);
      wsRef.current = ws;

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.event === 'ticket_update' || msg.event === 'new_message') {
            onRefresh();
          }
        } catch {
          // ignore malformed messages
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        if (isMounted) {
          reconnectTimer = setTimeout(connect, 3000);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    connect();

    return () => {
      isMounted = false;
      clearTimeout(reconnectTimer);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [(ticket as any).id, ticket.ticket_id, onRefresh]);

  return (
    <div className="bg-white rounded-lg shadow-lg p-6 sm:p-8">
      {/* Header */}
      <div className="mb-8">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-4">
          <div>
            <h1 className="text-3xl font-bold text-gray-900">{ticket.subject}</h1>
            <p className="text-sm text-gray-600 mt-1">
              Ticket ID: <code className="bg-gray-100 px-2 py-1 rounded">{ticket.ticket_number}</code>
            </p>
          </div>
          <div className="flex flex-col sm:flex-row gap-3 sm:items-center">
            <button
              onClick={onRefresh}
              disabled={isRefreshing}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {isRefreshing && (
                <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
              )}
              Refresh
            </button>
            <span className={`px-3 py-1 rounded-full text-sm font-semibold border ${statusColors[ticket.status]}`}>
              {ticket.status.replace('_', ' ').toUpperCase()}
            </span>
          </div>
        </div>

        {/* Ticket Details */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 py-4 border-t border-b border-gray-200">
          <div>
            <p className="text-xs text-gray-600 uppercase tracking-wide font-medium">Category</p>
            <p className="mt-1 text-sm font-medium text-gray-900">{ticket.category}</p>
          </div>
          <div>
            <p className="text-xs text-gray-600 uppercase tracking-wide font-medium">Priority</p>
            <p className="mt-1 text-sm font-medium text-gray-900">{ticket.priority}</p>
          </div>
          <div>
            <p className="text-xs text-gray-600 uppercase tracking-wide font-medium">Created</p>
            <p className="mt-1 text-sm font-medium text-gray-900">{formatDate(ticket.created_at)}</p>
          </div>
          <div>
            <p className="text-xs text-gray-600 uppercase tracking-wide font-medium">Updated</p>
            <p className="mt-1 text-sm font-medium text-gray-900">{formatDate(ticket.updated_at)}</p>
          </div>
        </div>
      </div>

      {/* Conversation Thread */}
      <div className="mb-8">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Conversation</h2>
        <div className="space-y-4 max-h-96 overflow-y-auto">
          {ticket.messages && ticket.messages.length > 0 ? (
            ticket.messages.map((msg: Message, idx: number) => (
              <div
                key={msg.id || idx}
                className={`p-4 rounded-lg ${
                  msg.sender_type === 'agent'
                    ? 'bg-gray-50 border border-gray-200'
                    : 'bg-white border border-gray-200'
                }`}
              >
                {/* Sender Info */}
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    {msg.sender_type === 'agent' && (
                      <span className="inline-block px-2 py-1 bg-blue-100 text-blue-700 text-xs font-medium rounded">
                        TechFlow Support
                      </span>
                    )}
                    {msg.agent_name && (
                      <span className="text-sm font-medium text-gray-700">{msg.agent_name}</span>
                    )}
                  </div>
                  <span className="inline-block px-2 py-1 bg-gray-100 text-gray-700 text-xs font-medium rounded">
                    {channelLabels[msg.channel] || msg.channel}
                  </span>
                </div>

                {/* Message Content */}
                <p className="text-gray-700 text-sm mb-2 whitespace-pre-wrap break-words">{msg.content}</p>

                {/* Timestamp */}
                <p className="text-xs text-gray-500">{formatDate(msg.timestamp)}</p>
              </div>
            ))
          ) : (
            <p className="text-center text-gray-500 py-8">No messages yet</p>
          )}
        </div>
      </div>

      {/* Action Links */}
      <div className="pt-6 border-t border-gray-200 flex flex-col sm:flex-row gap-3">
        <a
          href="/"
          className="px-4 py-2 text-sm font-medium text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors text-center"
        >
          Submit New Request
        </a>
        <button
          onClick={() => {
            navigator.clipboard.writeText(ticket.ticket_number);
            alert('Ticket number copied!');
          }}
          className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
        >
          Copy Ticket Number
        </button>
      </div>
    </div>
  );
}
