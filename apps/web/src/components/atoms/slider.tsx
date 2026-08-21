import * as React from 'react';
import { cn } from '../../lib/utils';

export type SliderProps = React.InputHTMLAttributes<HTMLInputElement>;

export function Slider({ className, type: _type, ...props }: SliderProps) {
  return <input type="range" className={cn('h-3 w-full cursor-pointer appearance-none rounded-full bg-primary/15 accent-primary outline-none transition focus:ring-2 focus:ring-primary/25 disabled:cursor-not-allowed disabled:opacity-75 [&::-webkit-slider-thumb]:size-5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border [&::-webkit-slider-thumb]:border-primary/15 [&::-webkit-slider-thumb]:bg-primary [&::-webkit-slider-thumb]:shadow-sm [&::-webkit-slider-thumb]:shadow-primary/20', className)} {...props} />;
}
