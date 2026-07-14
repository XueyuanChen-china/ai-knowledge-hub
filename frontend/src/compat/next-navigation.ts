import { useMemo } from "react";
import {
  useLocation,
  useNavigate,
  useParams as useReactRouterParams,
} from "react-router-dom";

export function usePathname() {
  return useLocation().pathname;
}

export function useParams<T extends Record<string, string>>() {
  return useReactRouterParams() as T;
}

export function useRouter() {
  const navigate = useNavigate();

  return useMemo(
    () => ({
      push: (href: string) => navigate(href),
      replace: (href: string) => navigate(href, { replace: true }),
      back: () => navigate(-1),
      refresh: () => window.location.reload(),
      prefetch: async (href: string) => {
        void href;
      },
    }),
    [navigate],
  );
}
