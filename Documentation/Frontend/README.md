This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.


## Website structure

/app
  /components
    LandingHero.tsx      // left-side: logo, info, motto
    LoginForm.tsx
    RegisterForm.tsx
    AuthCard.tsx         // contains both forms, toggle between
  /lib
    api.ts               // API logic for login/register
  /styles
    globals.css
  page.tsx               // main landing page

  




TODO LIST

  - Solve the page (see page/edit page / delete page (Modal support?) )
  -- Add the Players can edit page button
  -- Add a area on the Players form to add the allowed pages


- Solve the richtext editing/visualization


- Add a see/delete pages menu (for Writers)
 - They will be able to see all pages per world, etc...


- Maybe keep a log of who did what? and a menu for that - so we can visualize who edited/deleted etc...

- Add the search functionality (rich search per page title/content, concepts/groups and world)


-- The Import modal should import the data, but should allow to change the world info (name, description, data, logo, system).

-- Add a System settings menu

 - Here lets add the import/export worlds functionality
 - Here lets add the backup data functionality
 - Here lets add the import back up functionality
 - Here lets add some future configs (api codes etc...)



   text: "Normal text",
   numeric: "Only numbers",
   date: "Choose a date",
   image: "Upload an image",
   video: "YouTube video URL",
   audio: "Spotify audio URL",
   link: "External link",
   pdf: "Upload a PDF",
   page_ref: "Select related pages",
