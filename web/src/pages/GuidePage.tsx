import {
  ArrowRightLeft,
  type BookOpen,
  Compass,
  CopyCheck,
  FolderTree,
  GitBranch,
  History,
  Layers,
  LineChart,
  Percent,
  PieChart,
  Scale,
  Scissors,
  Sparkles,
  Tags,
  Target,
  TrendingUp,
  Upload,
  Wallet,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { PageHeader } from '@/components/layout/PageHeader'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

// A visual callout for a term worth pinning down the moment it's first
// used, rather than only in a glossary at the very end — so the
// definition is right where the reading flow needs it.
function Definition({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="my-4 rounded-lg border border-foreground/10 bg-muted/40 px-4 py-3">
      <p className="text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">Definition</p>
      <p className="mt-1 text-sm leading-relaxed">
        <span className="font-semibold text-foreground">{term}</span>
        <span className="text-muted-foreground"> — </span>
        {children}
      </p>
    </div>
  )
}

function Section({
  id,
  icon: Icon,
  title,
  children,
}: {
  id: string
  icon: typeof BookOpen
  title: string
  children: ReactNode
}) {
  return (
    <section id={id} className="scroll-mt-40 space-y-4 border-t border-border pt-10 first:border-t-0 first:pt-0">
      <h2 className="flex items-center gap-2.5 text-xl font-semibold tracking-tight text-foreground">
        <Icon className="size-5 text-muted-foreground" />
        {title}
      </h2>
      <div className="max-w-3xl space-y-4 text-sm leading-relaxed text-foreground/90">{children}</div>
    </section>
  )
}

function ExampleTable({ caption, columns, rows }: { caption: string; columns: string[]; rows: (string | number)[][] }) {
  return (
    <div className="my-4 overflow-x-auto rounded-lg border border-border">
      <table className="w-full border-collapse text-xs">
        <caption className="border-b border-border bg-muted/40 px-3 py-2 text-left font-medium text-muted-foreground">
          {caption}
        </caption>
        <thead>
          <tr className="border-b border-border bg-muted/20">
            {columns.map((column) => (
              <th key={column} className="px-3 py-2 text-left font-medium whitespace-nowrap text-muted-foreground">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        {/* Every table on this page puts its identifier in the first column —
            a posting_id, an account_id, an account kind — so `row[0]` is the
            row's natural key, and the column heading is the natural key of a
            cell within its row. Neither needs an index. */}
        <tbody className="font-mono">
          {rows.map((row) => (
            <tr key={String(row[0])} className="border-b border-border/60 last:border-0">
              {row.map((cell, cellIndex) => (
                <td key={columns[cellIndex]} className="px-3 py-1.5 whitespace-nowrap">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AccountKindTable() {
  const kinds: [string, string][] = [
    ['checking', 'An everyday spending account.'],
    ['savings', 'An interest-bearing deposit account.'],
    ['credit_card', 'A revolving line of credit — its balance is usually negative (money owed).'],
    [
      'vault',
      'A named sub-balance inside one of your own savings accounts, for banks that let you set money aside in labeled buckets without opening a separate account.',
    ],
    ['cash', 'Physical cash you track by hand.'],
    ['loan', 'Money you owe outside of a credit card — a mortgage, a personal loan, and so on.'],
    [
      'income_source',
      'Virtual — the generic "somewhere money came from" placeholder, used until a real payer is known. See Placeholder accounts below.',
    ],
    [
      'expense_payee',
      'Virtual — the generic "somewhere money went" placeholder, used until a real payee is known. See Placeholder accounts below.',
    ],
    [
      'external_investment',
      "A placeholder tracking a brokerage account's value — see Investments below for the pull-from-Investments-or-set-manually choice it's created with.",
    ],
    [
      'other_asset',
      'A manually-typed value (a car, a property) with no transaction history — just a number that counts toward net worth.',
    ],
  ]
  return <ExampleTable caption="Account kinds" columns={['Kind', 'What it means']} rows={kinds} />
}

// Two big, plain-language cards — no jargon, nothing from the Money or
// Investments tabs assumed. Meant to be the very first thing anyone reads,
// so it has to stand on its own for someone who has never used the app.
function OverviewCard({
  icon: Icon,
  title,
  tagline,
  points,
}: {
  icon: typeof BookOpen
  title: string
  tagline: string
  points: string[]
}) {
  return (
    <div className="flex-1 space-y-3 rounded-xl border border-foreground/10 bg-gradient-to-br from-muted/50 to-transparent p-6">
      <div className="flex items-center gap-2.5">
        <Icon className="size-5 text-muted-foreground" />
        <h3 className="text-lg font-semibold tracking-tight text-foreground">{title}</h3>
      </div>
      <p className="text-sm font-medium text-foreground/80">{tagline}</p>
      <ul className="space-y-1.5 text-sm leading-relaxed text-muted-foreground">
        {points.map((point) => (
          <li key={point} className="flex gap-2">
            <span className="text-foreground/30">•</span>
            {point}
          </li>
        ))}
      </ul>
    </div>
  )
}

function OverviewTab() {
  return (
    <div className="max-w-3xl space-y-8 text-sm leading-relaxed text-foreground/90">
      <p>
        This app keeps track of your money in two separate ways, side by side. Think of them as two different notebooks
        that mostly don't talk to each other.
      </p>

      <div className="flex flex-col gap-4 md:flex-row">
        <OverviewCard
          icon={Wallet}
          title="Money"
          tagline="Your everyday accounts."
          points={[
            'Your checking account, savings, credit cards — the accounts you use day to day.',
            'What you’ve been spending, and on what.',
            'What you’re setting aside for something (a trip, an emergency fund, a big purchase).',
            'Whether you’re on track against a monthly budget.',
          ]}
        />
        <OverviewCard
          icon={LineChart}
          title="Investments"
          tagline="Money you've put into the stock market."
          points={[
            'A brokerage account, where money is used to buy stocks and funds instead of just sitting in a bank.',
            'Whether that money is actually growing, and by how much.',
            'How that growth compares to simpler options, like leaving the same money in a regular savings account.',
            'What you might owe in tax if you sold everything today.',
          ]}
        />
      </div>

      <div className="rounded-lg border border-foreground/10 bg-muted/40 px-4 py-3">
        <p className="text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">Where they meet</p>
        <p className="mt-1 flex items-start gap-2 text-sm leading-relaxed">
          <Scale className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <span>
            <span className="font-semibold text-foreground">Net Worth</span> is the one place these two notebooks get
            added together — everything you own (bank accounts, investments, even something like a car you've typed in
            by hand) minus anything you owe, as one single number. Outside of that one number, the two sides run
            completely independently: nothing you do on the Money side changes your investments, and nothing you do with
            your investments changes your everyday budget.
          </span>
        </p>
      </div>

      <p>
        Everything else in this guide goes into more detail — the{' '}
        <span className="font-medium text-foreground">Money</span> tab above walks through how everyday accounts,
        budgets, and goals work; the <span className="font-medium text-foreground">Investments</span> tab does the same
        for the brokerage side. Neither one is required reading for the other.
      </p>
    </div>
  )
}

function MoneyTab() {
  return (
    <div className="space-y-10">
      <div className="rounded-lg border border-foreground/10 bg-gradient-to-br from-muted/60 to-transparent p-5">
        <p className="flex items-center gap-2 text-base font-semibold text-foreground">
          <Compass className="size-4 text-muted-foreground" />
          Read this once, and the rest of the Money side should feel obvious
        </p>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          Every page under the Money side of the app — Import, Accounts, Insights, Transactions, Rules, Categories,
          Tags, Budget, Goals, Net Worth — is a different view onto one shared record of what actually happened in your
          real accounts. Nothing is duplicated or separately maintained: change how a transaction is categorized in one
          place, and every chart, budget, and goal that touches it updates the same way. This page walks through that
          shared foundation once, end to end, so the vocabulary and mechanics used everywhere else make sense the first
          time you see them.
        </p>
      </div>

      <Section id="importing" icon={Upload} title="Importing data">
        <p>
          Data enters the app in exactly one place: the <span className="font-medium text-foreground">Import</span>{' '}
          page. Drag and drop a CSV export from one of your bank or card accounts, and a module in the backend reads it,
          translates whatever column names and codes that specific institution uses into this app's own shared
          vocabulary, and links the resulting rows to an account.
        </p>
        <p>
          That translation step is the important part. A checking account's CSV, a credit card's CSV, and a savings
          account's CSV rarely use the same column names or the same codes for the same things even within one bank —
          let alone across different banks. So each account type gets its own small, dedicated piece of code that knows
          exactly how to read its particular shape of file and turn it into the app's standard format. When you add an
          account whose CSV shape the app hasn't seen before, the Import page flags it with a warning instead of
          guessing — at that point, a new import module needs to be added in the backend code to teach the app that
          specific format before files from that account can be dropped in directly.
        </p>
        <p>
          Dropping in a file you've never imported before can auto-create the account it belongs to on the spot. For
          managing accounts directly — adding one by hand, renaming one, closing one — use the{' '}
          <span className="font-medium text-foreground">Accounts</span> page instead. Every account has a <em>kind</em>,
          which tells the rest of the app what it represents:
        </p>
        <AccountKindTable />
        <p>
          <code className="rounded bg-muted px-1 py-0.5 text-xs">external_investment</code> is the one exception to
          everything else on this page — it's an account that can be set up to never have transactions of its own
          imported into it. Instead, its value is pulled directly from the Investments side of the app, which is a
          completely separate module tracking brokerage accounts and other volatile assets. When creating one, you
          choose whether it pulls its value from Investments or is tracked manually like any other account (useful for a
          friend-managed fund, or a brokerage this app doesn't sync with directly) — see the Investments tab for what
          "pulling from Investments" actually means. Either way, this is the one link between the two halves of the app,
          and it only flows in one direction: Net Worth reads a live number from Investments; nothing in Investments
          ever reads accounting data back.
        </p>
      </Section>

      <Section id="ledger" icon={GitBranch} title="The ledger & postings">
        <Definition term="Ledger">
          the complete, append-only record of every transaction ever created from every CSV you've imported. Nothing
          else in the app stores its own separate numbers — every account balance, every net worth figure, every
          income/expense total is recomputed by walking through this record, a process this app calls{' '}
          <span className="font-medium text-foreground">replaying</span> the ledger. If two different charts ever
          disagreed about a total, that would mean one of them forgot to replay the same ledger — it's never a case of
          "which cached number is right," because nothing is cached.
        </Definition>
        <p>
          Once a CSV is standardized, every row in it becomes a <em>transaction</em>: one real economic event, like a
          paycheck landing or a card swipe. A transaction is never stored as a single row, though — it's represented as
          a small group of <em>postings</em> that all share one transaction id.
        </p>
        <Definition term="Posting">
          one row of one transaction: one account, one signed amount, one date. The amount is signed from that posting's{' '}
          <em>own</em> account's point of view — positive means money arrived in that account, negative means it left.
          People sometimes call one posting a <span className="font-medium text-foreground">leg</span> of its
          transaction — "the checking leg" versus "the credit card leg" of the same payment — but a leg isn't a
          different kind of object, it's just a posting looked at as one side of the transaction it belongs to.
        </Definition>
        <p>
          Every transaction always comes from somewhere and goes somewhere, so the postings belonging to one transaction
          always sum to exactly zero (in one currency). Two postings — one negative, one positive — is the ordinary
          case: a simple expense or a simple transfer. But nothing in the schema requires exactly two; a transaction can
          have any number of postings as long as they still sum to zero. That's what makes the{' '}
          <span className="font-medium text-foreground">splitting</span> feature possible, covered further down this
          page.
        </p>
        <p>Here's a real deposit, shown as the two postings it actually becomes once imported:</p>
        <ExampleTable
          caption="One $10,000 incoming wire → one transaction, two postings"
          columns={['posting_id', 'transaction_id', 'account', 'posted_at', 'amount', 'description']}
          rows={[
            ['txn-8f21a3:0', 'txn-8f21a3', 'Meridian Savings', '2026-01-28', '+10,000.00', 'Northgate Trust'],
            [
              'txn-8f21a3:1',
              'txn-8f21a3',
              'Uncategorized Income (virtual)',
              '2026-01-28',
              '-10,000.00',
              'Northgate Trust',
            ],
          ]}
        />
        <p>
          Both rows share one <code className="rounded bg-muted px-1 py-0.5 text-xs">transaction_id</code> (that's how
          an importer builds the pair), both carry the same date and description, and the two amounts cancel out. The
          savings account genuinely received $10,000 — but the <em>other</em> side of that money, whoever actually sent
          it, isn't known yet. That's exactly what the next section covers.
        </p>
        <p>
          Because the ledger is only ever <em>replayed</em>, never edited in place, it can always be thrown away and
          rebuilt: every CSV or statement PDF you've ever uploaded is separately archived, exactly as received, forever.
          If the derived ledger were ever to become corrupted or drift out of sync, the{' '}
          <span className="font-medium text-foreground">Rebuild ledger from raw archives</span> button on the Import
          page recomputes it from scratch from those originals — nothing is ever permanently lost.
        </p>
      </Section>

      <Section id="placeholders" icon={FolderTree} title="Placeholder accounts">
        <p>
          Every posting's counterparty is either one of your own real accounts, or — when the real counterparty isn't
          known yet — one of exactly two virtual placeholder accounts:
        </p>
        <ExampleTable
          caption="The two virtual placeholder accounts"
          columns={['account_id', 'kind', 'display name']}
          rows={[
            ['uncategorized:expense', 'expense_payee', 'Uncategorized Expense'],
            ['uncategorized:income', 'income_source', 'Uncategorized Income'],
          ]}
        />
        <p>
          These two accounts are hidden from the Transactions list — you'll never see "Uncategorized Income" listed as
          if it were one of your accounts. But they matter enormously for every income/expense chart: the pie chart, the
          monthly income-vs-expense chart, and every category total only ever count a posting whose <em>sibling</em>{' '}
          posting (the other side of its transaction) is still sitting on one of these two placeholders. The moment a{' '}
          <span className="font-medium text-foreground">transfer rule</span> repoints that placeholder to a real account
          (your credit card, another one of your own accounts), the transaction becomes a transfer between two accounts
          you hold — and every one of those charts correctly stops counting it, since moving your own money between your
          own accounts is neither income nor an expense.
        </p>
      </Section>

      <Section id="rules" icon={ArrowRightLeft} title="Transfer rules">
        <Definition term="Transfer rule">
          a trigger/action pair you define once: "if a posting on this account contains this text in its description,
          repoint its placeholder counterparty to this other account (and optionally set this category)." Transfer rules
          exist specifically to link two of your own accounts together — everything that isn't resolved by one stays
          pointed at one of the two virtual placeholders above.
        </Definition>
        <p>
          (Transfer rules are a different thing from the description-match <em>category patterns</em> covered in
          Automated categorization below — a transfer rule repoints a posting's counterparty automatically, with no
          confirmation step; a category pattern only ever suggests a category, and always needs to be applied and
          validated by hand.)
        </p>
        <p>
          Here's the mechanic that trips people up the most: paying off your own credit card from your own checking
          account isn't imported as one event — it's imported as <em>two separate transactions</em>, because your bank's
          statement and your card's statement are each imported independently and neither one knows the other exists.
          Each side still has its own unresolved placeholder leg:
        </p>
        <ExampleTable
          caption="One real transfer, imported as two transactions (four postings)"
          columns={['posting_id', 'transaction_id', 'account', 'amount', 'description']}
          rows={[
            ['txn-a1:0', 'txn-a1', 'Meridian Checking', '-85.00', 'Payment to Meridian Credit Card ending 4421'],
            [
              'txn-a1:1',
              'txn-a1',
              'Uncategorized Expense (virtual)',
              '+85.00',
              'Payment to Meridian Credit Card ending 4421',
            ],
            ['txn-b2:0', 'txn-b2', 'Meridian Credit Card', '+85.00', 'Payment Received – Thank You'],
            ['txn-b2:1', 'txn-b2', 'Uncategorized Income (virtual)', '-85.00', 'Payment Received – Thank You'],
          ]}
        />
        <p>
          A transfer rule only ever fixes <em>one</em> side: it matches on one account's own description and repoints{' '}
          <em>that transaction's</em> placeholder leg to whatever counterparty you name. It never automatically creates
          or triggers the mirror rule for the other side. So a pair like this needs two transfer rules if you want both
          transactions fully resolved:
        </p>
        <ul className="ml-4 list-disc space-y-1">
          <li>
            <span className="font-medium text-foreground">Transfer rule 1</span> — on Meridian Checking, if description
            contains "Payment to Meridian Credit Card", repoint to Meridian Credit Card.
          </li>
          <li>
            <span className="font-medium text-foreground">Transfer rule 2</span> — on Meridian Credit Card, if
            description contains "Payment Received", repoint to Meridian Checking.
          </li>
        </ul>
        <p>
          Add only one of the two, and <em>that</em> transaction alone gets reclassified as an internal transfer (and
          disappears from income/expense tracking) — while the other transaction is left exactly as it was, still
          showing up as an ordinary, uncategorized posting. Because of this, the app always proposes both directions
          together as one action when it detects a likely transfer — see the next section. Rules themselves are authored
          and edited on the <span className="font-medium text-foreground">Rules</span> page.
        </p>
      </Section>

      <Section id="transfer-suggestions" icon={ArrowRightLeft} title="Auto-detected transfer suggestions">
        <p>
          Writing every rule by hand isn't required — the app also scans for pairs of still-unresolved postings that
          look like the same transfer: the same amount, opposite sign, on two different real accounts, landing within a
          configurable number of days of each other (a deposit is checked against postings some number of days{' '}
          <em>before</em> it; a payment is checked against postings some number of days <em>after</em> it, since either
          side of a transfer can be the one that clears first). Matches are shown as suggestions on the Rules page's
          Suggestions tab, each with a proposed rule for both directions, fully editable before you add either one — the
          heuristic can be wrong (two unrelated transactions that happen to share an amount), so nothing is ever applied
          automatically.
        </p>
        <p>
          A suggestion that's genuinely not a transfer doesn't have to keep coming back: dismissing it with{' '}
          <span className="font-medium text-foreground">Not relevant</span> archives it instead of discarding it — it
          stops being proposed, but stays listed (and restorable) in a small archive at the bottom of the page, in case
          you change your mind later.
        </p>
      </Section>

      <Section id="duplicates" icon={CopyCheck} title="Duplicate detection">
        <p>
          A transfer suggestion is two <em>different</em> transactions, one per account, that belong together. A
          duplicate is the opposite problem: the <em>same</em> real-world purchase imported twice into the same account,
          because it reached this app through two different sources (a CSV export and a statement PDF, say) that each
          gave it their own transaction id. The Import page's Duplicates tab scans for groups of two or more
          transactions on one account with a matching amount, close dates, and similar-enough descriptions, and sorts
          them least-certain-first so the ones needing the closest look come first.
        </p>
        <p>
          Nothing is merged automatically. Reviewing a group lets you pick which transaction to keep — the others are
          dropped entirely, both their legs — with an optional description override for the one you keep. The same{' '}
          <span className="font-medium text-foreground">Not relevant</span> dismiss-and-archive mechanism from transfer
          suggestions applies here too: a group that isn't actually a duplicate can be archived instead of reviewed
          every time, and restored later if you change your mind.
        </p>
      </Section>

      <Section id="categorizing" icon={Tags} title="Categorizing & tags">
        <p>
          Once a posting's counterparty is resolved to a real account (or intentionally left as real income or a real
          expense against a placeholder), it's ready to categorize.
        </p>
        <Definition term="Category">
          what kind of spend or income a posting represents — Groceries, Salary, Rent. Categories are two levels deep at
          most: a top-level category, and optionally one subcategory beneath it (Food & Drink → Groceries). Every
          category is either an income category or an expense category, matching the sign of the postings it's used on.
          Categories, patterns, and a reference taxonomy all live on the{' '}
          <span className="font-medium text-foreground">Categories</span> page.
        </Definition>
        <Definition term="Tag">
          a label that cuts <em>across</em> categories, describing what a posting was part of rather than what kind of
          spend it was. A "Trip to Acadia" tag might sit on postings under Transport, Lodging, and Food & Drink all at
          once — so "how much did the Acadia trip cost, in total" is a normal question regardless of how each individual
          charge was categorized. The same idea applies to a one-off event like a "Christmas 2026" tag spanning Gifts,
          Food & Drink, and Travel. Tags are managed on their own{' '}
          <span className="font-medium text-foreground">Tags</span> page.
        </Definition>
      </Section>

      <Section id="automated-categorization" icon={Sparkles} title="Automated categorization">
        <p>
          Categorizing every transaction by hand is the main friction point in using a tool like this, so two
          independent, optional helpers exist — both are suggestion engines only; neither one ever silently categorizes
          anything:
        </p>
        <ul className="ml-4 list-disc space-y-1">
          <li>
            <span className="font-medium text-foreground">AI suggestions</span> — an LLM looks at a posting's
            description alongside examples of postings you've already categorized, and suggests a category and
            subcategory. Needs an API key entered on the <span className="font-medium text-foreground">Settings</span>{' '}
            page first; the button is disabled without one.
          </li>
          <li>
            <span className="font-medium text-foreground">Category patterns</span> — a plain, self-defined rule you
            write yourself: if a posting's description contains this text, suggest this category. It's the
            description-match equivalent of the AI suggestion, with no LLM involved.
          </li>
        </ul>
        <p>
          Both land the exact same way: as a temporary, colored suggestion on the row in Transactions (green for an AI
          suggestion, blue for a category pattern) — nothing is final yet. You click{' '}
          <span className="font-medium text-foreground">Apply</span> on a row (or run bulk suggestions across every
          uncategorized row currently in view) to stage a suggestion, then click{' '}
          <span className="font-medium text-foreground">Validate</span> to actually confirm it. Uncheck a suggestion you
          disagree with before validating, and it reverts to exactly what it was before — nothing about a posting
          changes until that validate step.
        </p>
      </Section>

      <Section id="splitting" icon={Scissors} title="Splitting">
        <p>
          The idea underlying everything above — a transaction is just a set of postings that sum to zero — has one more
          use: a single deposit can be divided into several categorized pieces instead of just two. A plain employer
          deposit of $1,000 is ordinarily two postings (-$1,000 against the virtual income placeholder, +$1,000 into
          your bank account). Splitting lets that same $1,000 arrival become three postings instead — still summing to
          zero — for example -$1,000 against the placeholder, +$700 categorized as Salary, and +$300 categorized as a
          health insurance reimbursement.
        </p>
        <p>
          This is exactly what the paystub import tool on the Import page automates: it reads an uploaded paystub PDF,
          matches its numbers against the real bank deposit(s) that arrived around payday (a single paystub can pay out
          into more than one account at once), and proposes a split for each deposit using whatever breakdown the
          paystub itself lists — separating salary from reimbursements and bonuses automatically, for you to review
          before applying.
        </p>
      </Section>

      <Section id="budgets" icon={Wallet} title="Budgets">
        <p>
          A budget assigns a target number — a "pocket" — to an expense category, so actual spend in that category can
          be measured against it. Budgets can be set two ways: a <em>general</em> budget applies the same target to
          every month alike, while a <em>per-month</em> budget sets a separate target for one specific month. Both are
          just numbers laid over the same categorized postings described above — nothing about a budget changes how a
          transaction is categorized or which charts it appears in.
        </p>
      </Section>

      <Section id="goals" icon={Target} title="Goals">
        <p>
          A goal is somewhere you're deliberately setting money aside — an emergency fund, a vacation, a big purchase —
          tracked separately from ordinary categorized spending. Under the hood, a goal is built from just two ideas.
        </p>
        <Definition term="Goal">
          a target: a name, a target amount, an optional target date, and a color. A goal never stores its own balance.
        </Definition>
        <Definition term="Contribution">
          one dated, signed entry — money going into a goal (positive) or coming back out of it (negative), on a real
          date. A goal's balance at any point in time is simply the sum of its own contributions up to that date, the
          same "replay, don't store" idea the rest of the app is built on. Contributions are never bucketed by month;
          "this month's contributions" is just a filter applied when displaying them, not a separate figure kept
          somewhere.
        </Definition>
        <p>
          Alongside every goal, the app also tracks{' '}
          <span className="font-medium text-foreground">unallocated money</span> — whatever your accounts already held
          when you started tracking, plus everything you've earned, minus everything you've spent, minus whatever you've
          already put toward any goal. This is never stored as if it were its own goal; it's a number computed fresh
          each time, the leftover after every real goal's contributions are subtracted out. It shows up as its own slice
          in a few charts for convenience, but it has no contributions of its own and nothing writes to it directly.
        </p>
        <p>
          Two optional automations build on top of this, and a goal's own contribution history is kept on a dedicated
          ledger tab so it's never just a running total you have to trust:
        </p>
        <ul className="ml-4 list-disc space-y-1">
          <li>
            <span className="font-medium text-foreground">Recurring additions</span> — an ordered list of rules that
            automatically move unallocated money into goals on a schedule (e.g. the 1st of every month), each either a
            fixed amount or a percentage of whatever's unallocated at the time. They run in priority order, so if there
            isn't enough unallocated money to fund every rule in full, the top-priority one is funded first and
            lower-priority ones get whatever's left over (or nothing). The lowest-priority rule may instead be set to
            take "the remainder" — whatever's left after every rule above it.
          </li>
          <li>
            <span className="font-medium text-foreground">Withdrawal automation</span> — a separate, ordered list that
            only ever triggers when unallocated money drops below zero (for example, after a large expense). When that
            happens, money is pulled back out of goals, in priority order, until unallocated is back to zero — never
            taking any single goal below zero itself. If every goal in the list is exhausted and unallocated is still
            negative, it's simply left negative with a warning shown, rather than the app inventing money that isn't
            there.
          </li>
        </ul>
      </Section>
    </div>
  )
}

function InvestmentsTab() {
  return (
    <div className="space-y-10">
      <div className="rounded-lg border border-foreground/10 bg-gradient-to-br from-muted/60 to-transparent p-5">
        <p className="flex items-center gap-2 text-base font-semibold text-foreground">
          <LineChart className="size-4 text-muted-foreground" />A separate module, built the same way
        </p>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          Investments tracks a brokerage account — holdings, trades, dividends, market prices — instead of bank
          transactions, but it's built on the exact same idea as the Money side: store what actually happened, and
          recompute everything else from that record on demand. Nothing here is a stored running total that could
          silently drift out of sync with its own history.
        </p>
      </div>

      <Section id="inv-overview" icon={Compass} title="Overview">
        <p>
          The only connection between this side and Money is one account kind,{' '}
          <code className="rounded bg-muted px-1 py-0.5 text-xs">external_investment</code> (see the Money tab's
          Importing data section): when you create one, you choose whether it pulls its current value live from here, or
          is tracked manually instead. Pulling only works once Investments actually has synced data to pull — the
          account-creation form greys the option out and explains why if it doesn't yet. Past that one link, the two
          sides never read from each other.
        </p>
        <p>
          The rest of this tab walks through the vocabulary behind the Investments pages —{' '}
          <span className="font-medium text-foreground">Performance</span>,{' '}
          <span className="font-medium text-foreground">Allocation</span>, and{' '}
          <span className="font-medium text-foreground">Taxes</span> — plus the{' '}
          <span className="font-medium text-foreground">Glossary</span> page, which lists every one of these terms
          alphabetically for a quick lookup once you already know roughly what you're looking for.
        </p>
      </Section>

      <Section id="inv-ledger" icon={History} title="Ledger & replay">
        <Definition term="Ledger">
          the complete history of every deposit, withdrawal, buy, sell, dividend, fee, and stock split ever recorded for
          this brokerage account, in one common format, in order. It's the investments side's counterpart to the Money
          side's own ledger — the one place data is durably written as a fact.
        </Definition>
        <Definition term="Replay">
          recomputing a result by walking through the ledger's history in order, instead of reading a value that was
          stored ahead of time. What you currently hold, what it cost, and what it's worth today are all worked out
          fresh this way every time a page loads — never a cached answer that could quietly go stale.
        </Definition>
        <p>
          Syncing pulls new events from your broker (via IBKR's Flex Web Service, the only source this app currently
          supports) and appends them to this history — it only ever adds, never edits a past event in place. The raw
          statement each sync fetches is archived exactly as received, so the derived ledger can always be thrown away
          and rebuilt from those originals if it's ever suspected of drifting out of sync.
        </p>
      </Section>

      <Section id="inv-lots" icon={Layers} title="Lots">
        <Definition term="Lot">
          one single purchase of a security, tracked on its own rather than blended into one running position. Buy the
          same stock on three different days and you have three lots, each with its own purchase date, price, and — once
          sold — its own realized gain. When shares are sold, the app consumes the <em>oldest</em> matching lots first
          (called FIFO — first in, first out), the same convention most brokers use for tax reporting.
        </Definition>
        <Definition term="Open lot">
          a purchase that still has shares remaining. Its gain or loss is <em>unrealized</em> — it isn't locked in yet,
          and moves with the current market price.
        </Definition>
        <Definition term="Closed lot">
          the part of a purchase that's been sold. It has its own sale price, holding period, and <em>realized</em> gain
          — final the moment the sale happens, and never affected by where the market goes afterward. A single sale can
          close multiple lots, or only part of one.
        </Definition>
        <Definition term="Term">
          LONG means the shares sold had been held at least 365 days; SHORT means less than that. It's purely a
          holding-period label here — see the Taxes section below for why it matters.
        </Definition>
      </Section>

      <Section id="inv-performance" icon={TrendingUp} title="Performance & benchmarks">
        <p>
          The Performance page exists to answer one question honestly: is investing actually working, separate from the
          fact that you keep putting money in? A rising balance is easy to mistake for a good decision, when most of the
          rise might just be a recent deposit landing. Every metric here is built to keep those two things apart.
        </p>
        <Definition term="XIRR">
          the single annual return rate that would exactly explain every deposit, withdrawal, and today's value, given
          exactly when each one happened. It measures the return on your actual money, timing included.
        </Definition>
        <Definition term="TWR (Time-Weighted Return)">
          the portfolio's investment return with deposit/withdrawal timing removed entirely — "how did the investments
          themselves perform," regardless of when money was added. This is what makes it fair to compare against a
          benchmark fund, which never had to guess when you'd add money.
        </Definition>
        <Definition term="NAV (Net Asset Value)">
          the value of one portfolio "unit," the same idea a mutual fund uses. A deposit buys more units, a withdrawal
          sells units, so the unit price itself only moves because of investment performance — never because money moved
          in or out. TWR is built by chaining this unit price's own growth over time.
        </Definition>
        <Definition term="Counterfactual">
          a replay of your exact same deposit/withdrawal history, but imagining the money had gone somewhere else
          instead — a high-yield savings account, or a benchmark index fund. Same dates, same amounts; only the
          destination changes. The gap between what actually happened and a counterfactual is the real-dollar answer to
          "was this worth it."
        </Definition>
        <p>
          <span className="font-medium text-foreground">Dollar alpha vs. HYSA</span> is today's portfolio value minus
          today's HYSA counterfactual value — a positive number means investing produced more real dollars than parking
          the same deposits in savings would have. <span className="font-medium text-foreground">Growth of $100</span>{' '}
          makes the same comparison a different way: every line starts at exactly 100 and ignores contribution timing
          entirely, so it compares <em>rates</em> of growth rather than dollar outcomes.{' '}
          <span className="font-medium text-foreground">Largest peak-to-trough</span> reports the worst drop the
          portfolio's own unit price has taken from a prior high, so far.
        </p>
      </Section>

      <Section id="inv-allocation" icon={PieChart} title="Allocation & cash">
        <Definition term="Drift">
          the gap between a holding's current share of the portfolio and the target share you've set for it. Positive
          means you're holding more of it than intended; negative means less.
        </Definition>
        <p>
          The Allocation page also tracks uninvested cash sitting in the account over time, and flags it once it's been
          sitting a while — a light flag past a week, a stronger one past two weeks — since idle cash is the one part of
          a portfolio that definitely isn't growing. Alongside the flag, it estimates what that same cash would be worth
          today had it been invested (in the benchmark, or at the HYSA rate) the moment it arrived instead, so the cost
          of leaving it uninvested is a real dollar figure, not just a vague sense that it's "sitting there."
        </p>
        <Definition term="Sitting since">
          every dollar that lands in the account is tracked from the day it arrives until it's actually invested — so if
          fresh cash lands on top of a balance that's already been sitting a while, that new money gets its own clock
          rather than inheriting the older one. "Sitting since" reports whichever dollar has been waiting the longest,
          not a blend, so a big recent deposit can never hide a small stale pocket of cash sitting right next to it. A
          withdrawal only removes dollars, so it can't make whatever's left look any fresher either — it's the same
          money it always was.
        </Definition>
        <p>
          The cash-over-time chart on the same page shows two more numbers built the same way. The dashed lines are what{' '}
          <em>currently</em>-sitting cash would be worth today had it been invested since it arrived — they track the
          real cash balance's own ups and downs, converging back to it the moment that cash actually gets invested.
          Below the chart, a separate figure adds up whatever's already been missed from cash that's
          <em> since</em> been invested — money that finished sitting is banked into this total once, at the moment it
          was deployed, and stays there; it only ever grows, since it's a running record of episodes that are already
          over, not a live number to react to today.
        </p>
      </Section>

      <Section id="inv-taxes" icon={Percent} title="Taxes">
        <p>
          The Taxes page always shows its full report, independent of the "Apply taxes" switch that appears on
          Performance and Allocation (that switch only controls whether <em>those</em> pages' own figures — the
          after-tax HYSA line, a lot's net-of-withholding dividends — reflect tax at all). Two tabs cover different
          halves of the same question:
        </p>
        <ul className="ml-4 list-disc space-y-1">
          <li>
            <span className="font-medium text-foreground">Report</span> — the year-by-year realized gains and dividends,
            an estimated tax bill, any flagged wash sales, and a live "if you sold everything today" preview.
          </li>
          <li>
            <span className="font-medium text-foreground">How it's taxed</span> — a plain-language explanation of what
            your chosen residency status actually means for dividends and capital gains.
          </li>
        </ul>
        <Definition term="Tax regime">
          which U.S. tax treatment applies — NRA (nonresident alien, e.g. F-1 student status) generally owes no U.S. tax
          on bank interest or on security sales at all; RESIDENT (e.g. H-1B, once the substantial-presence test is met)
          is taxed the same way a U.S. citizen is, on both. Chosen above the tab selector on the Taxes page, since it's
          the one setting that shapes everything in the report below it.
        </Definition>
        <Definition term="LTCG (Long-Term Capital Gains)">
          the lower tax rate a RESIDENT pays on a lot held longer than 365 days (or a qualified dividend), instead of
          their regular marginal rate. Never applies to an NRA, whose gains aren't U.S.-taxed at all regardless of how
          long anything was held.
        </Definition>
        <Definition term="Wash-sale">
          a loss sale flagged because the same security — or one on a declared similar-fund list — was bought back
          within 30 days before or after the sale. It's a mechanical proximity check meant to surface the risk, not an
          automatic adjustment to any number shown elsewhere on this dashboard.
        </Definition>
      </Section>
    </div>
  )
}

export function GuidePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get('tab') ?? 'overview'
  const setTab = (value: string) => setSearchParams(value === 'overview' ? {} : { tab: value })

  return (
    <div className="flex-1 overflow-y-auto">
      <PageHeader title="Guide" />

      <div className="mx-auto max-w-4xl px-8 py-8">
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="money">Money</TabsTrigger>
            <TabsTrigger value="investments">Investments</TabsTrigger>
          </TabsList>
          <TabsContent value="overview" className="pt-6">
            <OverviewTab />
          </TabsContent>
          <TabsContent value="money" className="pt-6">
            <MoneyTab />
          </TabsContent>
          <TabsContent value="investments" className="pt-6">
            <InvestmentsTab />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
