import React from 'react';
import { Loader2 } from 'lucide-react';
import clsx from 'clsx';

/**
 * Button Component
 * Reusable button with different variants and loading state
 */
export const Button = ({ 
  children, 
  variant = 'primary', 
  loading = false, 
  disabled = false,
  className = '',
  ...props 
}) => {
  const baseClasses = 'px-6 py-3 rounded-lg font-semibold transition-all duration-200 flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed';
  
  const variants = {
    primary: 'bg-gradient-to-r from-blue-600 to-blue-700 text-white shadow-md hover:shadow-lg transform hover:-translate-y-0.5',
    secondary: 'bg-white text-blue-600 border-2 border-blue-600 hover:bg-blue-50',
    danger: 'bg-red-600 text-white hover:bg-red-700',
    ghost: 'bg-transparent text-blue-600 hover:bg-blue-50',
  };

  return (
    <button
      className={clsx(baseClasses, variants[variant], className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading && <Loader2 className="w-5 h-5 animate-spin" />}
      {children}
    </button>
  );
};

/**
 * Card Component
 * Container with shadow and rounded corners
 */
export const Card = ({ children, className = '', ...props }) => {
  return (
    <div 
      className={clsx('bg-white rounded-xl shadow-lg border border-slate-200 p-6 transition-all duration-300 hover:shadow-xl', className)}
      {...props}
    >
      {children}
    </div>
  );
};

/**
 * Badge Component
 * Status indicator with different colors
 */
export const Badge = ({ children, variant = 'info', className = '' }) => {
  const variants = {
    success: 'bg-green-100 text-green-800',
    warning: 'bg-yellow-100 text-yellow-800',
    error: 'bg-red-100 text-red-800',
    info: 'bg-blue-100 text-blue-800',
    pending: 'bg-gray-100 text-gray-800',
  };

  return (
    <span className={clsx('inline-flex items-center px-3 py-1 rounded-full text-sm font-semibold', variants[variant], className)}>
      {children}
    </span>
  );
};

/**
 * LoadingSpinner Component
 * Animated loading indicator
 */
export const LoadingSpinner = ({ size = 'md', text = '' }) => {
  const sizes = {
    sm: 'w-4 h-4',
    md: 'w-8 h-8',
    lg: 'w-12 h-12',
    xl: 'w-16 h-16',
  };

  return (
    <div className="flex flex-col items-center justify-center gap-3">
      <Loader2 className={clsx(sizes[size], 'animate-spin text-blue-600')} />
      {text && <p className="text-slate-600 text-sm">{text}</p>}
    </div>
  );
};

/**
 * Alert Component
 * Notification message with different types
 */
export const Alert = ({ children, type = 'info', className = '' }) => {
  const types = {
    success: 'bg-green-50 border-green-200 text-green-800',
    error: 'bg-red-50 border-red-200 text-red-800',
    warning: 'bg-yellow-50 border-yellow-200 text-yellow-800',
    info: 'bg-blue-50 border-blue-200 text-blue-800',
  };

  return (
    <div className={clsx('border-l-4 p-4 rounded-r-lg', types[type], className)}>
      {children}
    </div>
  );
};

/**
 * ProgressBar Component
 * Shows upload or processing progress
 */
export const ProgressBar = ({ progress = 0, className = '' }) => {
  return (
    <div className={clsx('w-full bg-gray-200 rounded-full h-3 overflow-hidden', className)}>
      <div 
        className="h-full bg-gradient-to-r from-blue-500 to-blue-600 transition-all duration-300 ease-out rounded-full"
        style={{ width: `${Math.min(progress, 100)}%` }}
      />
    </div>
  );
};
