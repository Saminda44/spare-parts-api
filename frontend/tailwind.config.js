/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          blue:   "#4361EE",
          green:  "#2CC56F",
          amber:  "#FFC107",
          red:    "#EF4444",
          purple: "#7C3AED",
          teal:   "#06B6D4",
        },
        sidebar: "#1A1F37",
        surface: "#F8FAFC",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui"],
      },
    },
  },
  plugins: [],
}

