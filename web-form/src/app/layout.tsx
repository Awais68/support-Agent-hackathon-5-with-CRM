import type { Metadata, Viewport } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Support Ticket Form | TechFlow Analytics',
  description: 'Submit a support ticket and track your request status in real-time',
  openGraph: {
    type: 'website',
    url: 'http://localhost:3000',
    title: 'Support Ticket Form | TechFlow Analytics',
    description: 'Submit a support ticket and track your request status',
  },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body className="bg-gray-50 text-gray-900 antialiased">
        <div className="min-h-screen flex items-center justify-center py-8 px-4 sm:px-6 lg:px-8">
          <div className="w-full max-w-2xl">
            {children}
          </div>
        </div>
      </body>
    </html>
  );
}
