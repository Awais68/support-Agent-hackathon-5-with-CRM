import { z } from 'zod';

// Type definitions
export interface WebFormPayload {
  name: string;
  email: string;
  subject: string;
  category: 'general' | 'technical' | 'billing' | 'bug' | 'feedback';
  priority: 'low' | 'medium' | 'high' | 'urgent';
  message: string;
}

export interface SubmitResponse {
  ticket_number: string;
  message: string;
  estimated_response: string;
  tracking_url: string;
}

export interface Message {
  id: string;
  sender_type: 'customer' | 'agent';
  content: string;
  channel: 'email' | 'whatsapp' | 'web';
  timestamp: string;
  agent_name?: string;
}

export interface TicketDetail {
  ticket_id: string;
  ticket_number: string;
  status: 'open' | 'in_progress' | 'resolved' | 'escalated' | 'closed';
  subject: string;
  category: string;
  priority: string;
  created_at: string;
  updated_at: string;
  customer_email: string;
  messages: Message[];
}

// Validation schemas
const FormDataSchema = z.object({
  name: z.string().min(2, 'Name must be at least 2 characters'),
  email: z.string().email('Valid email required'),
  subject: z.string().min(5, 'Subject must be at least 5 characters'),
  category: z.enum(['general', 'technical', 'billing', 'bug', 'feedback']),
  priority: z.enum(['low', 'medium', 'high', 'urgent']),
  message: z.string().min(10, 'Message must be at least 10 characters').max(1000, 'Message cannot exceed 1000 characters'),
});

export type ValidatedFormData = z.infer<typeof FormDataSchema>;

// API client functions
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export async function submitSupportForm(data: WebFormPayload): Promise<SubmitResponse> {
  const response = await fetch(`${API_URL}/webhooks/webform`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Failed to submit form' }));
    throw new Error(error.message || 'Failed to submit support form');
  }

  return response.json();
}

export async function getTicketStatus(ticketId: string): Promise<TicketDetail> {
  const response = await fetch(`/api/tickets/${ticketId}`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  });

  if (!response.ok) {
    if (response.status === 404) {
      throw new Error('Ticket not found');
    }
    const error = await response.json().catch(() => ({ message: 'Failed to fetch ticket' }));
    throw new Error(error.message || 'Failed to fetch ticket status');
  }

  return response.json();
}

export function connectTicketWebSocket(ticketId: string): WebSocket {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const wsUrl = apiUrl.replace(/^http/, 'ws') + `/ws/tickets/${ticketId}`;
  return new WebSocket(wsUrl);
}

export { FormDataSchema };
