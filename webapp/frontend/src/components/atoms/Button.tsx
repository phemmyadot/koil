import type { ButtonHTMLAttributes } from "react";
import "./Button.css";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "primary" | "danger";
}

// Replaces .smallbtn / .tradebtn / header action buttons -- one component, variant prop
// instead of a different class name at every call site.
export function Button({ variant = "default", className, ...rest }: ButtonProps) {
  const cls = ["btn", `btn-${variant}`, className].filter(Boolean).join(" ");
  return <button type="button" className={cls} {...rest} />;
}
