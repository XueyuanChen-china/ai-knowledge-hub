import { forwardRef } from "react";
import type { ComponentPropsWithoutRef } from "react";
import { Link as RouterLink } from "react-router-dom";

type LinkProps = Omit<ComponentPropsWithoutRef<"a">, "href"> & {
  href: string;
  prefetch?: boolean;
  replace?: boolean;
  scroll?: boolean;
};

const Link = forwardRef<HTMLAnchorElement, LinkProps>(function Link(
  { href, prefetch, scroll, replace, ...props },
  ref,
) {
  void prefetch;
  void scroll;

  if (/^https?:\/\//.test(href)) {
    return <a ref={ref} href={href} {...props} />;
  }

  return <RouterLink ref={ref} to={href} replace={replace} {...props} />;
});

export default Link;
